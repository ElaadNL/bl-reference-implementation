import azure.functions as func

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from openadr3_client.bl.http_factory import BusinessLogicHttpClientFactory
from openadr3_client.oadr301._bl.client import BusinessLogicClient
from openadr3_client.oadr301.models.event.event import NewEvent
from openadr3_client.oadr301._vtn.interfaces.filters import TargetFilter
from openadr3_client.oadr301.models.event.event_payload import (
    EventPayload,
    EventPayloadType,
    EventPayloadDescriptor,
)
from openadr3_client._models.common.interval import Interval
from openadr3_client._models.common.interval_period import IntervalPeriod
from openadr3_client.oadr301.models.unit import Unit
from openadr3_client.oadr301.models.target import Target
from src.logger import logger
from openadr3_client.version import OADRVersion
from datetime import timezone

bp = func.Blueprint()

PROGRAM_ID = "602a77d6-c1e5-4c69-baec-3130b3bdc6bc"
VEN_NAME = "VEN_NAME_TARGETED_BY_EVENT_PRODUCED_BY_BL"
EXAMPLE_EAN_TARGET = "123456789012345678"

def _initialize_bl_client() -> BusinessLogicClient:
    """Initialize the BL client with the base URL of the VTN.

    Returns:
        BusinessLogicClient: The BL client.
    """
    bl_client: BusinessLogicClient = BusinessLogicHttpClientFactory.create_http_bl_client(
        vtn_base_url="https://flexnl-vtn-app-service.azurewebsites.net/", # Should be correct
        client_id=..., # your oauth client ID
        client_secret=..., # your oauth client token
        token_url="https://login.microsoftonline.com/b8a70a31-934f-4ff6-8e7a-2b646b7f480c/oauth2/v2.0/token", # oauth token URL, should be correct already.
        scopes=["4dd9217e-a334-4de7-9b0f-c3e9123dd08a/.default"], # OAuth scope, should be correct already.
        version=OADRVersion.V3_0_1, # Assuming OpenADR 3.0.1 for now.
    )
    return bl_client


async def _generate_events() -> NewEvent | None:
    """Generate an OpenADR event to be published by the BL.
    """
    # In this example, the event has three 15 minute chunks of different import capacity limits.
    intervals = (
        Interval(
            id=0,
            interval_period=IntervalPeriod(
                start=datetime.now(timezone=timezone.utc),
                duration=timedelta(minutes=15),
            ),
            payloads=(
                EventPayload(
                    type=EventPayloadType.IMPORT_CAPACITY_LIMIT,
                    values=(11.0,), # 11 kW limit for these 15 minutes.
                ),
            ),
        ),
        Interval(
            id=1,
            interval_period=IntervalPeriod(
                start=datetime.now(timezone=timezone.utc) + timedelta(minutes=15),
                duration=timedelta(minutes=15),
            ),
            payloads=(
                EventPayload(
                    type=EventPayloadType.IMPORT_CAPACITY_LIMIT,
                    values=(4.0,), # 4 kW limit for these 15 minutes.
                ),
            ),
        ),
        Interval(
            id=2,
            interval_period=IntervalPeriod(
                start=datetime.now(timezone=timezone.utc) + timedelta(minutes=15),
                duration=timedelta(minutes=15),
            ),
            payloads=(
                EventPayload(
                    type=EventPayloadType.IMPORT_CAPACITY_LIMIT,
                    values=(8.0,), # 8 kW limit for these 15 minutes.
                ),
            ),
        )
    )

    openadr_event = NewEvent(
        programID=PROGRAM_ID,
        event_name=f"bl-generated-event-{datetime.now().strftime('%d-%m-%Y')}",
        payload_descriptors=(
            EventPayloadDescriptor(
                payload_type=EventPayloadType.IMPORT_CAPACITY_LIMIT, units=Unit.KW
            ),
        ), # Provides metadata to the VEN about what this event contains (import capacity limit in this case, in kW).
        intervals=tuple(intervals),
        targets=(
            Target(type="VEN_NAME", values=(VEN_NAME,)), # The event is targeted to a specific VEN (for example, CPO).
            # The event is also targeted to a specific power service location, in this example, to a grid connection point (EAN18).
            # This is up to you to decide what you wish to target on.
            Target(type="POWER_SERVICE_LOCATION", values=(EXAMPLE_EAN_TARGET,)),
        ),
    )

    return openadr_event


async def _clean_up_old_events(bl_client: BusinessLogicClient) -> None:
    """Clean up old events from the VTN targeting the VEN of this BL that are going to be replaced by the new events."""
    # Get all events from the VTN
    events = bl_client.events.get_events(
        program_id=PROGRAM_ID,
        pagination=None,
        target=TargetFilter(target_type="VEN_NAME", target_values=[VEN_NAME]),
    )

    for event in events:
        bl_client.events.delete_event_by_id(event_id=event.id)
        logger.info("Deleted old event with id replaced by the BL: %s", event.id)


async def main() -> None:
    try:
        logger.info("Triggering BL function at %s", datetime.now(tz=UTC))
        event = await _generate_events()

        if not event:
            logger.warning(
                "No capacity limitation event could be constructed, skipping..."
            )
            return None

        bl_client = _initialize_bl_client()

        try:
            # Clean up the old events in the VTN that are going to be replaced by the new events
            await _clean_up_old_events(bl_client=bl_client)
            # Create the new event in the VTN.
            created_event = bl_client.events.create_event(new_event=event)
            logger.info("Created event with id: %s in VTN", created_event.id)
        except Exception as exc:
            logger.warning(
                "Exception occurred during event creation in the VTN", exc_info=exc
            )
    except Exception as exc:
        logger.warning("Exception occurred during function execution", exc_info=exc)

    logger.info("Python timer trigger function executed.")

# In this example, we trigger it periodically in an Azure function. But this can of course be any
# periodic scheduler you wish (asyncio job, lambda function, etc etc).
@bp.schedule(
    schedule="0 50 7 * * *",
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=False,
)
async def generate_events_for_tomorrow(myTimer: func.TimerRequest) -> None:
    await main()
