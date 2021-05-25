import asyncio
import base64
from dataclasses import dataclass
import hashlib
import hmac
import logging
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
EXBERRY_MASS_CANCEL = 'v1/exchange.market/massCancel'


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
    MassCancelRequest = 'Exberry.Integration:MassCancelRequest'
    MassCancelSuccess = 'Exberry.Integration:MassCancelSuccess'
    MassCancelFailure = 'Exberry.Integration:MassCancelFailure'


@dataclass
class ExberryIntegrationEnv(IntegrationEnvironment):
    username: str
    password: str
    tradingApiUrl: str
    adminApiUrl: str
    apiKey: str
    secret: str


def integration_exberry_main(
    env: 'ExberryIntegrationEnv',
    events: 'IntegrationEvents'):

    outbound_queue = asyncio.Queue()
    session_started = asyncio.Event()

    LOG.info(f"Preparing session...")
    order_book_depth = {
        'q': EXBERRY_ORDERBOOK_DEPTH,
        'sid': 0,
        'd': {}
    }
    outbound_queue.put_nowait(order_book_depth)

    async def enqueue_outbound(msg: dict):
        LOG.info(f"Enqueuing outbound message: {msg}")
        await outbound_queue.put(msg)


    async def request_session(api_key: str, secret_str: str, ws):
        time_str = str(int(time.time() * 1000))
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
        await ws.send_json(create_session)
        LOG.info("Waiting for session request to be confirmed...")


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
                await session_started.wait()
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
                    await env.queue.put(msg_data)
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
                'description': instrument['instrumentDescription'],
                'calendarId': instrument['calendarId'],
                'pricePrecision': str(instrument['pricePrecision']),
                'quantityPrecision': str(instrument['quantityPrecision']),
                'minQuantity': str(instrument['minQuantity']),
                'maxQuantity': str(instrument['maxQuantity']),
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
                                        'message': json_resp['message'],
                                        'name': json_resp['data'],
                                        'code': json_resp['code']
                                    })
                elif 'id' in json_resp:
                    return exercise(event.cid,
                                    'CreateInstrumentRequest_Success',
                                    {
                                        'instrumentId': json_resp['id']
                                    })
                else:
                    logging.warning(f"Unknown response ¯\\_(ツ)_/¯ : {json_resp}")


    @events.queue.message()
    async def process_inbound_messages(msg):

        if msg['q'] == EXBERRY_ORDERBOOK_DEPTH and msg['d']['messageType'] == 'Executed':
            msg_data = msg['d']
            return create(EXBERRY.ExecutionReport, {
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
            })

        elif msg['q'] == EXBERRY_PLACE_ORDER:
            if 'd' in msg and 'orderId' in msg['d']:
                msg_data = msg['d']
                # this is a place order ack
                return create(EXBERRY.NewOrderSuccess, {
                    'sid': msg['sid'],
                    'orderId': msg_data['orderId'],
                    'integrationParty': env.party
                })
            elif 'errorType' in msg:
                msg_data = msg['d']
                # this is a place order reject
                return create(EXBERRY.NewOrderFailure, {
                    'sid': msg['sid'],
                    'errorCode': msg_data['errorCode'],
                    'errorMessage': msg_data['errorMessage'],
                    'integrationParty': env.party
                })

        elif msg['q'] == EXBERRY_CREATE_SESSION:
            if 'sig' in msg and msg['sig'] == 1:
                LOG.info(f"Successfully established session!")
                session_started.set()

        elif msg['q'] == EXBERRY_CANCEL_ORDER:
            if 'd' in msg and 'orderId' in msg['d']:
                return create(EXBERRY.CancelOrderSuccess, {
                    'integrationParty': env.party,
                    'sid': msg['sid']
                })
            elif 'errorType' in msg:
                msg_data = msg['d']
                # this is a cancel order reject
                return create(EXBERRY.CancelOrderFailure, {
                    'integrationParty': env.party,
                    'sid': msg['sid'],
                    'errorCode': msg_data['errorCode'],
                    'errorMessage': msg_data['errorMessage'],
                })

        elif msg['q'] == EXBERRY_MASS_CANCEL:
            if 'd' in msg and 'numberOfOrders' in msg['d']:
                msg_data = msg['d']
                return create(EXBERRY.MassCancelSuccess, {
                    'integrationParty': env.party,
                    'sid': msg['sid'],
                    'numberOfOrders': msg_data['numberOfOrders']
                })
            elif 'errorType' in msg:
                msg_data = msg['d']
                # this is a mass cancel order reject
                return create(EXBERRY.MassCancelFailure, {
                    'integrationParty': env.party,
                    'sid': msg['sid'],
                    'errorCode': msg_data['errorCode'],
                    'errorMessage': msg_data['errorMessage'],
                })

    @events.ledger.contract_created(EXBERRY.MassCancelRequest)
    async def handle_mass_cancel_request(event):
        LOG.info(f"{EXBERRY.MassCancelRequest} created!")
        mass_cancel_req = mass_cancel(event.cdata)
        await outbound_queue.put(mass_cancel_req)
        return exercise(event.cid, 'Archive', {})


    async def fetch_token() -> str:
        async with ClientSession() as session:
            LOG.info("Requesting a token...")
            data_dict = {
                'email': env.username,
                'password': env.password,
            }
            LOG.info(f'Integration ==> Exberry: POST {data_dict}')
            token_url = env.adminApiUrl + '/auth/token'
            async with session.post(token_url, json=data_dict) as resp:
                json_resp = await resp.json()
                LOG.info(f'Integration <== Exberry: {json_resp}')
                return json_resp['token']


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

    def mass_cancel(mass_cancel_req):
        mass_cancel_json = {
            'd': {
                'instrument': mass_cancel_req['instrument']
            },
            'q': EXBERRY_MASS_CANCEL,
            'sid': mass_cancel_req['sid']
        }
        return mass_cancel_json



    async def connect():
        LOG.info(f"Connecting to the Exberry Trading API at {env.tradingApiUrl} ...")
        ws = await ClientSession().ws_connect(env.tradingApiUrl)
        LOG.info("...Connected to the Exberry Trading API")

        LOG.info(f"Preparing producer coroutine...")
        sender_task = asyncio.create_task(producer_coro(ws))

        LOG.info(f"Preparing consumer coroutine...")
        receiver_task = asyncio.create_task(consumer_coro(ws))

        LOG.info(f"Requesting market session...")
        await request_session(env.apiKey, env.secret, ws)

        asyncio.gather(*[sender_task, receiver_task])


    return connect()
