Automation Tests
===================

These Daml scripts can be used to run a full test of the integration

These tests work by running actions in a Daml Script and then waiting for the integration
the desired result using the `waitUntil` and `waitQuery` functions. After a
specified time, if the desired result has not occured, the test is considered failed and exits.

## Running locally
To run tests locally:

1. Start a Sandbox with `daml start` in this folder.
2. Start the integration
3. Run the test:
    `daml script --dar .daml/dist/exberry-integration-tests-1.0.0.dar --script-name Test.integrationTest --ledger-host localhost --ledger-port 6865`

## Running on Hub
To run tests locally:

1. Create a ledger
2. Create the 'Exchange' party
3. Deploy the integration as the 'Exchange' party
4. Download the `ledger-parties.json` and `participants.json` files from the 'Ledger Settings' page
3. Run the test:
    `daml script --participant-config participants.json --json-api --dar .daml/dist/exberry-integration-tests-1.0.0.dar --script-name Test:runIntegrationTest --input-file ledger-parties.json`
