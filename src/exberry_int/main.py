import logging

from dazl import create, exercise

from daml_dit_if.api import \
    IntegrationEnvironment, IntegrationEvents

from .types import EXBERRY, Endpoints
from .exberry_integration import ExberryIntegration, ExberryIntegrationEnv

LOG = logging.getLogger('dabl-integration-exberry')

def integration_exberry_main(
    env: 'ExberryIntegrationEnv',
    events: 'IntegrationEvents'):

    integration = ExberryIntegration(env, LOG)

    ### Incoming Contracts

    @events.ledger.contract_created(EXBERRY.NewOrderRequest)
    async def handle_new_order_request(event):
        LOG.info(f"{EXBERRY.NewOrderRequest} created!")
        order_data = event.cdata['order']
        order = Endpoints.make_create_order_req(order_data)
        await integration.enqueue_outbound(order)
        return exercise(event.cid, 'Archive', {})

    @events.ledger.contract_created(EXBERRY.CancelOrderRequest)
    async def handle_cancel_order_request(event):
        LOG.info(f"{EXBERRY.CancelOrderRequest} created!")
        cancel_order_req = Endpoints.make_cancel_order_req(event.cdata)
        await integration.enqueue_outbound(cancel_order_req)
        return exercise(event.cid, 'Archive', {})

    @events.ledger.contract_created(EXBERRY.CreateInstrumentRequest)
    async def handle_create_instrument_request(event):
        LOG.info(f"{EXBERRY.CreateInstrumentRequest} created!")
        instrument = event.cdata

        LOG.info('Creating instrument...')
        data_dict = Endpoints.make_create_instrument_req(instrument)

        requested_calendar_id = data_dict['calendarId']
        calendar_resp = await integration.get_admin({}, f"{Endpoints.Calendars}/{requested_calendar_id}")
        if 'code' in calendar_resp and calendar_resp['code'] == 10005:
            LOG.info('calendarId not found, using default...')
            calendars = await integration.get_admin({}, Endpoints.Calendars)
            data_dict['calendarId'] = calendars[0]['id']

        json_resp = await integration.post_admin(data_dict, Endpoints.Instruments)
        if 'data' in json_resp:
            return exercise(event.cid,
                            'CreateInstrumentRequest_Failure',
                            {
                                'message': json_resp['message'],
                                'name': json_resp['data'],
                                'code': json_resp['code']
                            })
        elif 'id' in json_resp:
            # await integration.request_session()
            # await integration.subscribe_to_order_book_depth()
            await integration.close_connection()
            return exercise(event.cid,
                            'CreateInstrumentRequest_Success',
                            {
                                'instrumentId': json_resp['id']
                            })
        else:
            LOG.warning(f"Unknown response ¯\\_(ツ)_/¯ : {json_resp}")

    @events.ledger.contract_created(EXBERRY.MassCancelRequest)
    async def handle_mass_cancel_request(event):
        LOG.info(f"{EXBERRY.MassCancelRequest} created!")
        mass_cancel_req = Endpoints.make_mass_cancel_req(event.cdata)
        await integration.enqueue_outbound(mass_cancel_req)
        return exercise(event.cid, 'Archive', {})

    ### Incoming Exberry Messages

    @events.queue.message()
    async def process_inbound_messages(msg: dict):
        if not 'q' in msg:
            LOG.warning('Message does not have endpoint qualifier.')

        msg_handlers = {
            Endpoints.CreateSession: handle_create_session_msg,
            Endpoints.OrderBookDepth: handle_orderbook_depth_msg,
            Endpoints.PlaceOrder: handle_place_order_msg,
            Endpoints.CancelOrder: handle_cancel_order_msg,
            Endpoints.MassCancel: handle_mass_cancel_msg,
        }

        endpoint = msg['q']
        if endpoint in msg_handlers:
            return await msg_handlers[endpoint](msg)
        else:
            LOG.warning(f'{endpoint} does not have a message handler.')

    async def handle_create_session_msg(msg: dict):
        if 'sig' in msg and msg['sig'] == 1:
            LOG.info(f"Successfully established session!")
            await integration.start_session()

    async def handle_orderbook_depth_msg(msg: dict):
        if 'errorType' in msg:
            error_type = msg['errorType']
            error_code = msg['d']['errorCode']
            error_message = msg['d']['errorMessage']
            LOG.error(f'Orderbook Error - Type: {error_type}, Code: {error_code}, Message: {error_message}')

            # error code 400 indicates there is already an existing subscription, otherwise resubscibe
            if error_code != 400:
                LOG.warning('Possibly lost order book subscription, resubscribing...')
                await integration.subscribe_to_order_book_depth()
        else:
            msg_data = msg['d']
            if 'trackingNumber' in msg_data:
                integration.set_last_tracking_number(msg_data['trackingNumber'])
            if msg_data['messageType'] == 'Executed':
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

    async def handle_place_order_msg(msg: dict):
        if 'd' in msg and 'orderId' in msg['d']:
            msg_data = msg['d']
            LOG.info(f'Received order acknowledgement, creating {EXBERRY.NewOrderSuccess}')
            return create(EXBERRY.NewOrderSuccess, {
                'sid': msg['sid'],
                'orderId': msg_data['orderId'],
                'integrationParty': env.party
            })

        elif 'errorType' in msg:
            msg_data = msg['d']
            LOG.info(f'Received order failure, creating {EXBERRY.NewOrderFailure}')
            return create(EXBERRY.NewOrderFailure, {
                'sid': msg['sid'],
                'errorCode': msg_data['errorCode'],
                'errorMessage': msg_data['errorMessage'],
                'integrationParty': env.party
            })

    async def handle_cancel_order_msg(msg: dict):
        if 'd' in msg and 'orderId' in msg['d']:
            LOG.info(f'Received cancel order acknowledgement, creating {EXBERRY.CancelOrderSuccess}')
            return create(EXBERRY.CancelOrderSuccess, {
                'integrationParty': env.party,
                'sid': msg['sid']
            })
        elif 'errorType' in msg:
            msg_data = msg['d']
            LOG.info(f'Received cancel order failure, creating {EXBERRY.CancelOrderFailure}')
            return create(EXBERRY.CancelOrderFailure, {
                'integrationParty': env.party,
                'sid': msg['sid'],
                'errorCode': msg_data['errorCode'],
                'errorMessage': msg_data['errorMessage'],
            })

    async def handle_mass_cancel_msg(msg: dict):
        if 'd' in msg and 'numberOfOrders' in msg['d']:
            LOG.info(f'Received mass cancel success, creating {EXBERRY.MassCancelSuccess}')
            msg_data = msg['d']
            return create(EXBERRY.MassCancelSuccess, {
                'integrationParty': env.party,
                'sid': msg['sid'],
                'numberOfOrders': msg_data['numberOfOrders']
            })
        elif 'errorType' in msg:
            msg_data = msg['d']
            LOG.info(f'Received mass cancel failure, creating {EXBERRY.MassCancelSuccess}')
            return create(EXBERRY.MassCancelFailure, {
                'integrationParty': env.party,
                'sid': msg['sid'],
                'errorCode': msg_data['errorCode'],
                'errorMessage': msg_data['errorMessage'],
            })

    return integration.connect()
