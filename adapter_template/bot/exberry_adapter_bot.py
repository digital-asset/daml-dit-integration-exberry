import logging
import os
from datetime import datetime

import dazl
from dazl import create, exercise, exercise_by_key

dazl.setup_default_logger(logging.INFO)

class EXBERRY:
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

def main():
    url = os.getenv('DAML_LEDGER_URL')
    exchange = os.getenv('DAML_LEDGER_PARTY')

    exchange_party = "Exchange" if not exchange else exchange

    network = dazl.Network()
    network.set_config(url=url)

    logging.info(f'Integration will run under party: {exchange_party}')

    client = network.aio_party(exchange_party)

    @client.ledger_ready()
    def say_hello(event):
        logging.info("DA Marketplace <> Exberry adapter is ready!")

    @client.ledger_created(EXBERRY.NewOrderSuccess)
    async def handle_new_order_success(event):
        logging.info(f"Handling NewOrderSuccess")

    @client.ledger_created(EXBERRY.NewOrderFailure)
    async def handle_new_order_failure(event):
        logging.info(f"Handling NewOrderFailure")

    @client.ledger_created(EXBERRY.CancelOrderSuccess)
    async def handle_cancel_order_success(event):
        logging.info(f"Handling CancelOrderSuccess")

    @client.ledger_created(EXBERRY.CancelOrderFailure)
    async def handle_cancel_order_failure(event):
        logging.info(f"Handling CancelOrderFailure")

    @client.ledger_created(EXBERRY.ExecutionReport)
    async def handle_execution_report(event):
        logging.info(f"Handling execution report")

    network.run_forever()

if __name__ == "__main__":
    logging.info("DA Marketplace <> Exberry adapter is starting up...")

    main()
