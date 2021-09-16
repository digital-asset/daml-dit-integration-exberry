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


class Endpoints:
    CreateSession = 'exchange.market/createSession'
    PlaceOrder = 'v1/exchange.market/placeOrder'
    OrderBookDepth = 'v1/exchange.market/orderBookDepth'
    CancelOrder = 'v1/exchange.market/cancelOrder'
    MassCancel = 'v1/exchange.market/massCancel'
    Instruments = 'instruments'

    ValidResponseEndpoints = { OrderBookDepth, PlaceOrder, CreateSession, CancelOrder, MassCancel }

    @staticmethod
    def make_create_order_req(order_data) -> dict:
        """ Create order request message from EXBERRY.NewOrderRequest """
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
            'q': Endpoints.PlaceOrder,
            'sid': order_data['mpOrderId']
        }
        return order_json


    @staticmethod
    def make_cancel_order_req(cancel_data) -> dict:
        """ Create cancel order request message from EXBERRY.CancelOrderRequest """
        cancel_order_json = {
            'd': {
                'mpOrderId': cancel_data['mpOrderId'],
                'userId': cancel_data['userId'],
                'instrument': cancel_data['instrument']
            },
            'q': Endpoints.CancelOrder,
            'sid': cancel_data['mpOrderId'],
        }
        return cancel_order_json


    @staticmethod
    def make_mass_cancel_req(mass_cancel_data) -> dict:
        """ Create mass cancel request message from EXBERRY.MassCancelRequest """
        mass_cancel_json = {
            'd': {
                'instrument': mass_cancel_data['instrument']
            },
            'q': Endpoints.MassCancel,
            'sid': mass_cancel_data['sid']
        }
        return mass_cancel_json


    @staticmethod
    def make_create_instrument_req(instrument_data) -> dict:
        """ Create instrument request message from EXBERRY.CreateInstrumentRequest """
        return {
            'symbol': instrument_data['symbol'],
            'quoteCurrency': instrument_data['quoteCurrency'],
            'description': instrument_data['instrumentDescription'],
            'calendarId': instrument_data['calendarId'],
            'pricePrecision': str(instrument_data['pricePrecision']),
            'quantityPrecision': str(instrument_data['quantityPrecision']),
            'minQuantity': str(instrument_data['minQuantity']),
            'maxQuantity': str(instrument_data['maxQuantity']),
            'status': instrument_data['status']
        }

