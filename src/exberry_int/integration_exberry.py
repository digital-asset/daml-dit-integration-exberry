import asyncio
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import logging
import os
import time

from aiohttp import ClientSession, WSMsgType
from dazl import create, exercise

from daml_dit_if.api import \
    IntegrationEnvironment, IntegrationEvents

LOG = logging.getLogger('dabl-integration-exberry')

EXBERRY_CREATE_SESSION = 'exchange.market/createSession'
EXBERRY_PLACE_ORDER = 'v1/exchange.market/placeOrder'
EXBERRY_ORDERBOOK_DEPTH = 'v1/exchange.market/orderBookDepth'
EXBERRY_CANCEL_ORDER = 'v1/exchange.market/cancelOrder'


class EXBERRY:
    TradingAPIConnection = 'Exberry.Integration:TradingAPIConnection'
    NewOrderRequest = 'Exberry.Integration:NewOrderRequest'
    NewOrderSuccess = 'Exberry.Integration:NewOrderSuccess'
    NewOrderFailure = 'Exberry.Integration:NewOrderFailure'
    CancelOrderRequest = 'Exberry.Integration:CancelOrderRequest'
    CancelOrderSuccess = 'Exberry.Integration:CancelOrderSuccess'
    CancelOrderFailure = 'Exberry.Integration:CancelOrderFailure'
    CreateInstrumentRequest = 'Exberry.Integration:CreateInstrumentRequest'
    Instrument = 'Exberry.Integration:Instrument'
    FailedInstrumentRequest = 'Exberry.Integration:FailedInstrumentRequest'
    ExecutionReport = 'Exberry.Integration:ExecutionReport'


@dataclass
class ExberryIntegrationEnv(IntegrationEnvironment):
    username: str
    password: str
    clientId: str
    tradingApiUrl: str
    adminApiUrl: str
    tokenUrl: str
    apiKey: str
    secret: str
    interval: int


def integration_exberry_main(
    env: 'ExberryIntegrationEnv',
    events: 'IntegrationEvents'):

    outbound_queue = asyncio.Queue()
    inbound_queue = asyncio.Queue()

    async def enqueue_outbound(msg: dict):
        LOG.info(f"Enqueuing outbound message: {msg}")
        await outbound_queue.put(msg)


    async def dequeue_inbound() -> dict:
        msg = await inbound_queue.get()
        LOG.info(f"Dequeued inbound message: {msg}")
        return msg


    async def request_session(api_key: str, secret_str: str):
        time_str = int(time.time() * 1000)
        LOG.info(f"Computing signature...")
        signature = compute_signature(api_key, secret_str, time_str)
        LOG.info(f"...OK")
        create_session = {
            'q': EXBERRY_CREATE_SESSION,
            'sid': 0,
            'd': {
                'apiKey': api_key,
                'timestamp': time_str,
                'signature': signature
            }
        }
        await enqueue_outbound(create_session)


    async def request_market_data():
        order_book_depth = {
            'q': EXBERRY_ORDERBOOK_DEPTH,
            'sid': 0,
            'd': {}
        }
        await enqueue_outbound(order_book_depth)


    async def producer_coro(ws):
        try:
            while True:
                LOG.info("Awaiting next message to send...")
                request_to_send = await outbound_queue.get()
                LOG.info(f"Integration --> Exberry: {request_to_send}")
                await ws.send_json(request_to_send)
        except Exception as e:
            LOG.exception(f"Error in producer coroutine: {e}")
        finally:
            LOG.info("Ending producer coroutine...")


    async def consumer_coro(ws):
        try:
            async for msg in ws:
                LOG.info(f"Received message {msg}")
                if msg.type == WSMsgType.TEXT:
                    msg_data = msg.json()
                    LOG.info(f"Integration <-- Exberry: {msg_data}")
                    await inbound_queue.put(msg_data)
                elif msg.type == WSMsgType.ERROR:
                    LOG.error("Error message type! Breaking consumer loop...")
                    break
                else:
                    LOG.warning(f"Unhandled messge type: {msg.type}. Ignoring...")
        except Exception as e:
            LOG.exception(f"Error in consumer coroutine: {e}")
        finally:
            LOG.info("Ending consumer coroutine...")


    @events.ledger.contract_created(EXBERRY.NewOrderRequest)
    async def handle_new_order_request(event):
        LOG.info(f"{EXBERRY.NewOrderRequest} created!")
        order_data = event.cdata['order']
        order = create_order(order_data)
        await outbound_queue.put(order)
        return exercise(event.cid, 'Archive', {})


    @events.ledger.contract_created(EXBERRY.CancelOrderRequest)
    async def handle_cancel_order_request(event):
        LOG.info(f"{EXBERRY.CancelOrderRequest} created!")
        # TODO: make cancel request and put in queue
        cancel_order_req = cancel_order(event.cdata)
        await outbound_queue.put(cancel_order_req)
        return exercise(event.cid, 'Archive', {})


    @events.ledger.contract_created(EXBERRY.CreateInstrumentRequest)
    async def handle_create_instrument_request(event):
        LOG.info(f"{EXBERRY.CreateInstrumentRequest} created!")
        instrument = event.cdata
        token = await fetch_token()

        async with ClientSession() as session:
            LOG.info('Creating instrument...')
            data_dict = {
                'symbol': instrument['symbol'],
                'quoteCurrency': instrument['quoteCurrency'],
                'instrumentDescription': instrument['instrumentDescription'],
                'calendarId': instrument['calendarId'],
                'pricePrecision': int(instrument['pricePrecision']),
                'quantityPrecision': int(instrument['quantityPrecision']),
                'minQuantity': float(instrument['minQuantity']),
                'maxQuantity': float(instrument['maxQuantity']),
                'status': instrument['status']
            }
            LOG.info(f'Integration ==> Exberry: POST {data_dict}')
            async with session.post(f'{env.adminApiUrl}/instruments',
                                    json=data_dict,
                                    headers={'Authorization': f'Bearer {token}'}) as resp:
                json_resp = await resp.json()
                LOG.info(f'Integration <== Exberry: {json_resp}')
                if 'data' in json_resp:
                    return exercise(event.cid,
                                    'CreateInstrumentRequest_Failure',
                                    {
                                        'message': json_resp['data']['data'],
                                        'name': json_resp['data']['name'],
                                        'code': json_resp['data']['code']
                                    })
                elif 'id' in json_resp:
                    return exercise(event.cid,
                                    'CreateInstrumentRequest_Success',
                                    {
                                        'instrumentId': json_resp['id']
                                    })
                else:
                    logging.warning(f"Unknown response ¯\\_(ツ)_/¯ : {json_resp}")


    @events.queue.message(env.interval)
    async def process_inbound_messages(msg):
        if not inbound_queue.empty():
            LOG.info(f"Will process {inbound_queue.qsize()} messages...")

        commands = []
        if msg['q'] == EXBERRY_ORDERBOOK_DEPTH and msg['d']['messageType'] == 'Executed':
            msg_data = msg['d']
            commands.append(create(EXBERRY.ExecutionReport, {
                'sid': msg['sid'],
                'eventId': msg_data['eventId'],
                'eventTimestamp': str(msg_data['eventTimestamp']),
                'instrument': msg_data['instrument'],
                'trackingNumber': msg_data['trackingNumber'],
                'makerMpId': msg_data['makerMpId'],
                'makerMpOrderId': msg_data['makerMpOrderId'],
                'makerOrderId': msg_data['makerOrderId'],
                'takerMpId': msg_data['takerMpId'],
                'takerMpOrderId': msg_data['takerMpOrderId'],
                'takerOrderId': msg_data['takerOrderId'],
                'matchId': msg_data['matchId'],
                'executedQuantity': msg_data['executedQuantity'],
                'executedPrice': msg_data['executedPrice'],
                'integrationParty': env.party,
            }))

        elif msg['q'] == EXBERRY_PLACE_ORDER:
            if 'd' in msg and 'orderId' in msg['d']:
                msg_data = msg['d']
                # this is a place order ack
                commands.append(create(EXBERRY.NewOrderSuccess, {
                    'sid': msg['sid'],
                    'orderId': msg_data['orderId'],
                    'integrationParty': env.party
                }))
            elif 'errorType' in msg:
                msg_data = msg['d']
                # this is a place order reject
                commands.append(create(EXBERRY.NewOrderFailure, {
                    'sid': msg['sid'],
                    'errorCode': msg_data['errorCode'],
                    'errorMessage': msg_data['errorMessage'],
                    'integrationParty': env.party
                }))

        elif msg['q'] == EXBERRY_CREATE_SESSION:
            if 'sig' in msg and msg['sig'] == 1:
                LOG.info(f"Successfully established session!")
                LOG.info(f"Requesting market data subscription...")
                await request_market_data()

        elif msg['q'] == EXBERRY_CANCEL_ORDER:
            if 'd' in msg and 'orderId' in msg['d']:
                commands.append(create(EXBERRY.CancelOrderSuccess, {
                    'integrationParty': env.party,
                    'sid': msg['sid']
                }))
            elif 'errorType' in msg:
                msg_data = msg['d']
                # this is a cancel order reject
                commands.append(create(EXBERRY.CancelOrderFailure, {
                    'integrationParty': env.party,
                    'sid': msg['sid'],
                    'errorCode': msg_data['errorCode'],
                    'errorMessage': msg_data['errorMessage'],
                }))

        return commands


    async def fetch_token() -> str:
        async with ClientSession() as session:
            LOG.info("Requesting a token...")
            data_dict = {
                'grant_type': 'password',
                'username': env.username,
                'password': env.password,
                'audience': 'bo-gateway',
                'scope': 'Instrument/create ' \
                    'Instrument/update ' \
                    'Instrument/get ' \
                    'Instrument/list ' \
                    'HaltResume/halt ' \
                    'HaltResume/resume ' \
                    'HaltResume/haltAll ' \
                    'HaltResume/resumeAll',
                'client_id': env.clientId
            }
            LOG.info(f'Integration ==> Exberry: POST {data_dict}')
            async with session.post(env.tokenUrl, data=data_dict) as resp:
                json_resp = await resp.json()
                LOG.info(f'Integration <== Exberry: {json_resp}')
                return json_resp['access_token']


    def compute_signature(api_key, secret_str: str, time_str: str):
        message_str = f'''"apiKey":"{api_key}","timestamp":"{time_str}"'''
        message = bytes(message_str, 'utf-8')
        secret = bytes(secret_str, 'utf-8')
        sig = hmac.new(secret, message, digestmod=hashlib.sha256).digest().hex()
        LOG.info(f"signature is {sig}")
        return sig


    def create_order(order_data):
        order_json = {
            'd' : {
                'orderType': order_data['orderType'],
                'instrument': order_data['instrument'],
                'quantity': float(order_data['quantity']),
                'price': float(order_data['price']),
                'side': order_data['side'],
                'timeInForce': order_data['timeInForce'],
                'mpOrderId': order_data['mpOrderId'],
                'userId': order_data['userId'],
            },
            'q': EXBERRY_PLACE_ORDER,
            'sid': order_data['mpOrderId']
        }
        return order_json


    def cancel_order(cancel_req):
        cancel_order_json = {
            'd': {
                'mpOrderId': cancel_req['mpOrderId'],
                'userId': cancel_req['userId'],
                'instrument': cancel_req['instrument']
            },
            'q': EXBERRY_CANCEL_ORDER,
            'sid': cancel_req['mpOrderId'],
        }
        return cancel_order_json


    async def connect():
        LOG.info(f"Connecting to the Exberry Trading API at {env.tradingApiUrl} ...")
        ws = await ClientSession().ws_connect(env.tradingApiUrl)
        LOG.info("...Connected to the Exberry Trading API")

        LOG.info(f"Preparing producer coroutine...")
        sender_task = asyncio.create_task(producer_coro(ws))

        LOG.info(f"Preparing consumer coroutine...")
        receiver_task = asyncio.create_task(consumer_coro(ws))

        LOG.info(f"Preparing session coroutine...")
        session_task = asyncio.create_task(request_session(env.apiKey, env.secret))

        asyncio.gather(*[session_task, sender_task, receiver_task])


    return connect()
