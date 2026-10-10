"""Brighton & Hove's public Mendix collection checker (explicit Bartec dates)."""

import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from waste_collection_schedule import Collection, Icons  # type: ignore[attr-defined]
from waste_collection_schedule.exceptions import SourceArgumentNotFound

TITLE = "Brighton & Hove City Council"
DESCRIPTION = "Actual collection dates from Brighton & Hove City Council."
URL = "https://www.brighton-hove.gov.uk/rubbish-recycling-and-streets/bins-and-collections/check-your-bin-box-and-caddy-collection-days"
TEST_CASES = {"11 West Drive": {"postcode": "BN2 0GD", "uprn": "000022064770"}}

BASE = "https://enviroservices.brighton-hove.gov.uk/"
TIMEOUT = (8, 30)
ICONS = {
    "Refuse": Icons.GENERAL_WASTE,
    "Recycling": Icons.RECYCLING,
    "Food waste": Icons.ORGANIC,
    "Garden Waste": Icons.GARDEN,
}


def _value(fields, key):
    return fields.get(key, {}).get("value")


def _valid_uprn(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[0-9]{1,12}", value) is not None
        and int(value) > 0
    )


class Source:
    def __init__(self, postcode: str, uprn: str | int):
        self.postcode = str(postcode).strip().upper()
        self.uprn = str(uprn)
        if not self.postcode or not _valid_uprn(self.uprn):
            raise ValueError("A postcode and positive ASCII numeric UPRN are required")

    def fetch(self) -> list[Collection]:
        with requests.Session() as session:
            request_id = 3
            session.get(BASE + "link/collections", timeout=TIMEOUT).raise_for_status()
            init = session.post(
                BASE + "xas/",
                json={
                    "action": "get_session_data",
                    "params": {
                        "hybrid": False,
                        "offline": False,
                        "referrer": None,
                        "profile": "",
                        "timezoneoffset": -60,
                        "timezoneId": "Europe/London",
                        "preferredLanguages": ["en-GB", "en"],
                        "version": 2,
                    },
                },
                timeout=TIMEOUT,
            )
            init.raise_for_status()
            initial = init.json()
            csrf = initial["csrftoken"]
            collection = next(
                obj
                for obj in initial["objects"]
                if obj["objectType"] == "Collections.Collection"
            )
            address = next(
                obj
                for obj in initial["objects"]
                if obj["objectType"] == "BHCCTheme.Address"
            )
            cg, ag = collection["guid"], address["guid"]
            path = initial["instructions"][0]["args"]["FormPath"]
            locale = initial["locale"]["code"]
            form = session.get(
                BASE + f"pages/{locale}/{path}",
                params={initial["cachebust"]: ""},
                timeout=TIMEOUT,
            )
            form.raise_for_status()

            def operation(marker):
                match = re.search(
                    re.escape(marker) + r'.*?"operationId":"([^"]+)"', form.text
                )
                if not match:
                    raise ValueError(f"Missing Mendix operation for {marker}")
                return match.group(1)

            search_id = operation(
                '"buttonId":"p.BHCCTheme.Template_SearchFieldOnly.actionButton3"'
            )
            select_id = operation('"attribute":"BHCCTheme.AddressTemp_SelectedAddress"')
            next_id = operation(
                '"buttonId":"p.BartecCollective.Jobs_Get_Combined.actionButton5"'
            )

            def execute(op, changes, objects, params, valid):
                nonlocal request_id
                response = session.post(
                    BASE + "xas/",
                    json={
                        "action": "runtimeOperation",
                        "operationId": op,
                        "changes": changes,
                        "objects": objects,
                        "params": params,
                        "profiledata": {},
                        "validationGuids": valid,
                    },
                    headers={
                        "x-csrf-token": csrf,
                        "x-mx-reqtoken": f"{int(time.time())}-{request_id}",
                    },
                    timeout=TIMEOUT,
                )
                request_id += 1
                response.raise_for_status()
                return response.json()

            matches = execute(
                search_id,
                {ag: {"SearchString": {"value": self.postcode}}},
                [collection, address],
                {"Address": {"guid": ag}},
                [ag],
            )
            candidates = [
                (guid, fields)
                for guid, fields in matches["changes"].items()
                if _valid_uprn(_value(fields, "uprn"))
                and int(_value(fields, "uprn")) == int(self.uprn)
            ]
            if len(candidates) != 1:
                raise SourceArgumentNotFound("uprn", self.uprn)
            selected_guid, selected_fields = candidates[0]
            if (
                self.postcode.replace(" ", "")
                != str(_value(selected_fields, "postcode") or "")
                .replace(" ", "")
                .upper()
            ):
                raise ValueError("Council address postcode did not match")
            selected_change = {
                **matches["changes"],
                ag: {
                    **matches["changes"].get(ag, {}),
                    "SearchString": {"value": self.postcode},
                    "BHCCTheme.AddressTemp_SelectedAddress": {"value": selected_guid},
                },
            }
            selection = execute(
                select_id,
                selected_change,
                [address, collection] + matches["objects"],
                {"Address": {"guid": ag}, "Collection": {"guid": cg}},
                [ag, cg],
            )
            if not _value(
                selection.get("changes", {}).get(cg, {}), "DisplayCollectionsButton"
            ):
                raise ValueError("Council did not accept the selected address")
            merged = {
                **selected_change,
                ag: {**selected_change[ag], **selection.get("changes", {}).get(ag, {})},
                cg: {
                    **selected_change.get(cg, {}),
                    **selection.get("changes", {}).get(cg, {}),
                },
            }
            data = execute(
                next_id,
                merged,
                [address, collection] + matches["objects"],
                {"Collection": {"guid": cg}},
                [cg, ag],
            )
            if any(
                "trouble connecting to our online service"
                in str(i.get("args", {}).get("MessageContent", ""))
                for i in data.get("instructions", [])
            ):
                data = execute(
                    next_id,
                    merged,
                    [address, collection] + matches["objects"],
                    {"Collection": {"guid": cg}},
                    [cg, ag],
                )
                if any(
                    "trouble connecting to our online service"
                    in str(i.get("args", {}).get("MessageContent", ""))
                    for i in data.get("instructions", [])
                ):
                    raise ValueError(
                        "Council still reports trouble connecting to our online service"
                    )
            rows = [
                obj
                for obj in data.get("objects", [])
                if obj.get("objectType") == "Collections.CollectionsReturned"
            ]
            if not rows:
                raise ValueError("Council returned no collection rows")
            today = datetime.now(ZoneInfo("Europe/London")).date()
            result = []
            for row in rows:
                attrs = row["attributes"]
                if _value(attrs, "Collections.CollectionsReturned_Collection") != cg:
                    raise ValueError("Council returned collections for another address")
                row_uprn = _value(attrs, "UPRN")
                if not _valid_uprn(row_uprn) or int(row_uprn) != int(self.uprn):
                    raise ValueError("Council returned collections for another UPRN")
                name = _value(attrs, "Collection_type")
                status = _value(attrs, "BartecStatus")
                if (
                    name == "Garden Waste"
                    and status == "You are not currently subscribed to garden waste"
                ):
                    continue
                if name not in ICONS or not isinstance(status, str) or not status:
                    raise ValueError(
                        f"Unexpected council collection type/status: {name}/{status}"
                    )
                if not _value(attrs, "BartecJobID"):
                    raise ValueError(f"Missing council job for {name}")
                is_past = _value(attrs, "IsPast")
                if not isinstance(is_past, bool):
                    raise ValueError(f"Invalid IsPast value for {name}")
                date_text = _value(attrs, "Collection_Date")
                if not isinstance(date_text, str):
                    raise ValueError(f"Missing collection date for {name}")
                collection_date = datetime.strptime(date_text, "%d/%m/%Y, %H:%M").date()
                if (
                    status != "Closed-Complete"
                    and not is_past
                    and collection_date >= today
                ):
                    result.append(
                        Collection(date=collection_date, t=name, icon=ICONS[name])
                    )
            if not result:
                raise ValueError("Council returned no future collections")
            if "Refuse" not in {item.type for item in result}:
                raise ValueError("Council returned no future Refuse collection")
            if "Recycling" not in {item.type for item in result}:
                raise ValueError("Council returned no future Recycling collection")
            return sorted(result, key=lambda item: (item.date, item.type))
