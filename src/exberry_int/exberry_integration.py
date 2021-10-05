import asyncio
from aiohttp import ClientSession, WSMsgType
from dataclasses import dataclass
import hashlib
import hmac
import itertools
from typing import Type
import time
from logging import Logger
from enum import Enum
import time
from .types import Endpoints

from daml_dit_if.api import \
    IntegrationEnvironment

@dataclass
class ExberryIntegrationEnv(IntegrationEnvironment):
    username: str
    password: str
    tradingApiUrl: str
    adminApiUrl: str
    apiKey: str
    secret: str


class OutboundPriority(Enum):
    MARKET_RECONNECT = -1
    DEQUEUED_MESSAGE = 0
    NEW_MESSAGE = 1


class MultiplePriorityQueue:
    def __init__(self, priorities: Type[Enum]):
        self.queue = asyncio.PriorityQueue()
        self.priority_counters = dict([(prio, itertools.count()) for prio in priorities])

    async def put(self, priority, item):
        await self.queue.put([priority.value, next(self.priority_counters[priority]), item])

    async def get(self):
        _, _, item = await self.queue.get()
        return item


class ExberryIntegration:
    """ Handles connection to Exberry along with sending and receiving messages """
    def __init__(self, env: 'ExberryIntegrationEnv', logger: Logger):
        self.outbound_queue = MultiplePriorityQueue(OutboundPriority)
        self.session_started = asyncio.Event()
        self.order_book_canceled = asyncio.Event()
        self.env = env
        self.last_tracking_number = None # Optional[int]
        self.logger = logger
        self.ws = None
        self.token = ""
        self.token_refresh_time = time.time()


    def set_last_tracking_number(self, tracking_number: int):
        """
        Set the tracking number that the integration will use to request an order book
        stream starting from
        """
        self.last_tracking_number = tracking_number


    async def set_session_started(self):
        """ Indicate that the integration has established an Exberry websocket session """
        self.session_started.set()


    async def set_order_book_canceled(self):
        """ Indicate that the integration has canceled the order book subscription """
        self.order_book_canceled.set()


    def _order_book_depth_subscription_request(self):
        """ Creates a subscription message to subscribe to the OrderBook stream at the latest entry """
        data = { 'trackingNumber': self.last_tracking_number } if self.last_tracking_number else {}
        return {
            'q': Endpoints.OrderBookDepth,
            'sid': 0,
            'd': data
        }


    async def subscribe_to_order_book_depth(self):
        """ Enqueues a message to be sent that will subscribe to Exberry's OrderBook stream """
        request = self._order_book_depth_subscription_request()
        await self.enqueue_outbound(request, OutboundPriority.MARKET_RECONNECT)


    async def post_admin(self, data_dict: dict, endpoint: str) -> dict:
        """ POST a message to the Exberry Admin API """
        token = await self._fetch_token()
        async with ClientSession() as session:
            self.logger.info(f'Integration ==> Exberry: POST {data_dict}')
            async with session.post(f'{self.env.adminApiUrl}/{endpoint}',
                                    json=data_dict,
                                    headers={'Authorization': f'Bearer {token}'}) as resp:
                return await resp.json()


    async def get_admin(self, data_dict: dict, endpoint: str) -> dict:
        """ GET a message to the Exberry Admin API """
        token = await self._fetch_token()
        async with ClientSession() as session:
            self.logger.info(f'Integration ==> Exberry: GET {data_dict}')
            async with session.get(f'{self.env.adminApiUrl}/{endpoint}',
                                    json=data_dict,
                                    headers={'Authorization': f'Bearer {token}'}) as resp:
                return await resp.json()


    async def enqueue_outbound(self, msg: dict, priority: OutboundPriority = OutboundPriority.NEW_MESSAGE):
        """ Enqueue a message to be send through the Exberry websocket """
        self.logger.info(f"Enqueuing outbound message: {msg} with priority {priority}")
        await self.outbound_queue.put(priority, msg)


    async def _dequeue_outbound(self) -> dict:
        """ Retrieve next message to be sent through the Exberry websocket """
        return await self.outbound_queue.get()


    async def _fetch_token(self) -> str:
        """ Retrieve a token to be used with the Exberry Admin API """
        if time.time() < self.token_refresh_time:
            return self.token

        async with ClientSession() as session:
            self.logger.info("Requesting a token...")
            data_dict = {
                'email': self.env.username,
                'password': self.env.password,
            }
            self.logger.info(f'Integration ==> Exberry Admin API: POST {data_dict}')
            token_url = self.env.adminApiUrl + '/auth/token'
            async with session.post(token_url, json=data_dict) as resp:
                json_resp = await resp.json()
                self.logger.info(f'Integration <== Exberry Admin API: {json_resp}')
                self.token = json_resp['token']
                self.token_refresh_time = time.time() + json_resp['expiresIn'] - 10
                return json_resp['token']


    def _compute_signature(self, api_key, secret_str: str, time_str: str):
        message_str = f'''"apiKey":"{api_key}","timestamp":"{time_str}"'''
        message = bytes(message_str, 'utf-8')
        secret = bytes(secret_str, 'utf-8')
        sig = hmac.new(secret, message, digestmod=hashlib.sha256).digest().hex()
        self.logger.info(f"signature is {sig}")
        return sig


    async def resubscribe(self):
        """ Cancels the current OrderBookDepth stream, creates a new client session and resubscribes """
        if self.ws:
            unsubscribe = {
                'q': Endpoints.OrderBookDepth,
                'sid': 0,
                'sig': 3
            }
            self.logger.info(f"Cancelling OrderBook subscription...")
            self.order_book_canceled.clear()
            await self.ws.send_json(unsubscribe)

            self.logger.info(f"Requesting new client session...")
            self.session_started.clear()
            await self._request_session(self.env.apiKey, self.env.secret, self.ws)

            await self.session_started.wait()
            await self.order_book_canceled.wait()

            self.logger.info(f"Resubscribing to OrderBook...")
            await self.subscribe_to_order_book_depth()

        else:
            self.logger.error('Tried to resubscribe before ws was initialized...')


    async def _request_session(self, api_key: str, secret_str: str, ws):
        """ Request a websocket session to the Exberry server """
        time_str = str(int(time.time() * 1000))
        self.logger.info(f"Computing signature...")
        signature = self._compute_signature(api_key, secret_str, time_str)
        self.logger.info(f"...OK")

        create_session = {
            'q': Endpoints.CreateSession,
            'sid': 0,
            'd': {
                'apiKey': api_key,
                'timestamp': time_str,
                'signature': signature
            }
        }
        self.logger.info(f"Integration ===> Exberry: {create_session}")
        await ws.send_json(create_session)
        self.logger.info("Waiting for session request to be confirmed...")


    async def _producer_coro(self, ws):
        """ Send queued outbound messages through the Exberry websocket """
        request_to_send = None
        try:
            while True:
                self.logger.debug("Awaiting next message to send...")
                request_to_send = await self._dequeue_outbound()

                await self.session_started.wait()

                self.logger.info(f"Integration ===> Exberry: {request_to_send}")
                await ws.send_json(request_to_send)
                request_to_send = None
        except Exception as e:
            self.logger.exception(f"Error in producer coroutine: {e}")
        finally:
            self.logger.info("Ending producer coroutine...")
            if request_to_send:
                self.logger.info('Message dequeued but not sent, prioritizing on reconnect...')
                await self.enqueue_outbound(request_to_send, OutboundPriority.DEQUEUED_MESSAGE)


    def _check_message_validity(self, msg: dict):
        """
        Ensure that only messages that require a ledger command are put on the
        integration's queue
        """
        if not 'q' in msg: return False
        endpoint = msg['q']
        if endpoint == Endpoints.PlaceOrder or endpoint == Endpoints.CancelOrder:
            return 'errorType' in msg or 'd' in msg and 'orderId' in msg['d']
        elif endpoint == Endpoints.MassCancel:
            return 'errorType' in msg or 'd' in msg and 'numberOfOrders' in msg['d']
        elif endpoint == Endpoints.OrderBookDepth:
            if 'errorType' in msg or 'sig' in msg:
                return True
            else:
                return 'd' in msg and msg['d']['messageType'] == 'Executed' and 'makerMpId' in msg['d']
        else:
            return endpoint in Endpoints.ValidResponseEndpoints


    async def _consumer_coro(self, ws):
        """ Place incoming websocket messages onto the integration queue for processing in the main function """
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    msg_data = msg.json()
                    if self._check_message_validity(msg_data):
                        self.logger.info(f"Integration <=== Exberry: {msg_data}")
                        await self.env.queue.put(msg_data)
                    else:
                        self.logger.debug(f"Message {msg_data} not necessary for contract creation, ignoring...")

                elif msg.type == WSMsgType.ERROR:
                    self.logger.error(f"Error message type with error: {msg.data} Breaking consumer loop...")
                    break
                else:
                    self.logger.warning(f"Unhandled messge type: {msg.type}. Ignoring...")
        except Exception as e:
            self.logger.exception(f"Error in consumer coroutine: {e}")
        finally:
            self.logger.info("Ending consumer coroutine...")
            if ws.closed:
                raise Exception("Consumer routine ended with websocket lost")
            else:
                raise Exception("Consumer routine ended unexpectedly")


    async def connect(self):
        """ Start the Exberry Websocket connection and consumer/producer tasks """
        tasks = []
        ws = None
        try:
            self.session_started.clear()

            self.logger.info(f"Connecting to the Exberry Trading API at {self.env.tradingApiUrl} ...")
            ws = await ClientSession().ws_connect(self.env.tradingApiUrl)
            self.ws = ws

            self.logger.info("...Connected to the Exberry Trading API")

            self.logger.info(f"Preparing session...")
            await self.subscribe_to_order_book_depth()

            self.logger.info(f"Preparing producer coroutine...")
            sender_task = asyncio.create_task(self._producer_coro(ws))

            self.logger.info(f"Preparing consumer coroutine...")
            receiver_task = asyncio.create_task(self._consumer_coro(ws))

            self.logger.info(f"Requesting market session...")
            await self._request_session(self.env.apiKey, self.env.secret, ws)

            self.logger.info(f"Starting coroutines...")
            tasks = [sender_task, receiver_task]
            await asyncio.gather(*tasks)

        except Exception as e:
            self.logger.warn(f'connection error: {e}')

        finally:
            self.logger.info('Cancelling tasks and cleaing up...')
            for t in tasks: t.cancel()
            if ws:
                await ws.close()
                self.ws = None

            self.logger.warn('Attempting reconnect in 2 seconds...')
            await asyncio.sleep(2)
            await self.connect()

