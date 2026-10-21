"""Brighton Mendix source: isolated protocol fixtures, no household identifiers."""

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.append(
    str(
        Path(__file__).resolve().parents[1]
        / "custom_components/waste_collection_schedule"
    ),
)
from waste_collection_schedule import Icons
from waste_collection_schedule.source import brighton_hove_gov_uk


class FrozenDatetime(datetime):
    utc_now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.utc_now.astimezone(tz)


@pytest.fixture(autouse=True)
def council_clock():
    FrozenDatetime.utc_now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    with patch.object(brighton_hove_gov_uk, "datetime", FrozenDatetime):
        yield FrozenDatetime


class Response:
    def __init__(self, data, text=""):
        self.data = data
        self.text = text

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class CouncilSession:
    def __init__(self):
        self.calls = []
        self.transient_next = False
        self.error_with_rows = False
        self.is_past = {}
        self.search_postcode = "BN2 1AA"
        self.search_results = [
            ("v1", "11 West Drive, Brighton, BN2 1AA", "000011111111"),
            ("v2", "13 West Drive, Brighton, BN2 1AA", "000022222222"),
        ]
        self.results = [
            ("Recycling", "22/12/2026, 08:00", "Not Started", "000011111111", "101"),
            ("Refuse", "23/12/2026, 08:00", "Not Started", "11111111", "102"),
            ("Food waste", "24/12/2026, 08:00", "Not Started", "11111111", "103"),
            (
                "Garden Waste",
                "25/12/2026, 01:00",
                "You are not currently subscribed to garden waste",
                "000011111111",
                None,
            ),
        ]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        if url.endswith("/link/collections"):
            return Response(None)
        return Response(
            None,
            """\"buttonId\":\"p.BHCCTheme.Template_SearchFieldOnly.actionButton3\",\"config\":{\"operationId\":\"search-id\"}\"buttonId\":\"p.BartecCollective.Jobs_Get_Combined.actionButton5\",\"config\":{\"operationId\":\"next-id\"}\"attribute\":\"BHCCTheme.AddressTemp_SelectedAddress\",\"onChange\":{\"config\":{\"operationId\":\"select-id\"}}""",
        )

    def post(self, url, **kw):
        payload = kw["json"]
        self.calls.append(("post", url, kw))
        if payload["action"] == "get_session_data":
            return Response(
                {
                    "csrftoken": "session-csrf",
                    "cachebust": "cache",
                    "locale": {"code": "en_GB"},
                    "objects": [
                        {"objectType": "DeepLink.DeepLink", "guid": "deep"},
                        {"objectType": "Collections.Collection", "guid": "collection"},
                        {"objectType": "BHCCTheme.Address", "guid": "address"},
                    ],
                    "instructions": [
                        {
                            "args": {
                                "FormPath": "BartecCollective/Jobs_Get_Combined.page.xml"
                            }
                        }
                    ],
                }
            )
        if payload["operationId"] == "search-id":
            return Response(
                {
                    "changes": {
                        guid: {
                            "display": {"value": address},
                            "uprn": {"value": uprn},
                            "postcode": {"value": self.search_postcode},
                        }
                        for guid, address, uprn in self.search_results
                    },
                    "objects": [
                        {"objectType": "BHCCTheme.AddressTempTable", "guid": guid}
                        for guid, _, _ in self.search_results
                    ],
                }
            )
        if payload["operationId"] == "select-id":
            return Response(
                {
                    "changes": {
                        "address": {
                            "display": {"value": self.search_results[0][1]},
                            "uprn": {"value": self.search_results[0][2]},
                            "Search_DisplaySelectedAddress": {"value": True},
                        },
                        "collection": {"DisplayCollectionsButton": {"value": True}},
                    },
                    "objects": [],
                }
            )
        if payload["operationId"] == "next-id":
            if self.transient_next:
                self.transient_next = False
                return Response(
                    {
                        "objects": [],
                        "instructions": [
                            {
                                "type": "text_message",
                                "args": {
                                    "MessageContent": "Sorry, we're having trouble connecting to our online service right now. Try again later."
                                },
                            }
                        ],
                    }
                )
            return Response(
                {
                    "instructions": (
                        [
                            {
                                "args": {
                                    "MessageContent": "Sorry, we're having trouble connecting to our online service right now. Try again later."
                                }
                            }
                        ]
                        if self.error_with_rows
                        else []
                    ),
                    "objects": [
                        {
                            "objectType": "Collections.CollectionsReturned",
                            "guid": f"row-{i}",
                            "attributes": {
                                "Collection_type": {"value": t},
                                "Collection_Date": {"value": dt},
                                "BartecStatus": {"value": status},
                                "IsPast": {"value": self.is_past.get(i, False)},
                                "UPRN": {"value": uprn},
                                "BartecJobID": {"value": job},
                                "Collections.CollectionsReturned_Collection": {
                                    "value": "collection"
                                },
                            },
                        }
                        for i, (t, dt, status, uprn, job) in enumerate(self.results)
                    ],
                }
            )
        raise AssertionError(f"Unexpected operation {payload['operationId']}")


def test_fetch_uses_council_dates_and_excludes_unsubscribed_garden():
    session = CouncilSession()
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        result = brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    assert [(entry.type, entry.date, entry.icon) for entry in result] == [
        ("Recycling", date(2026, 12, 22), Icons.RECYCLING),
        ("Refuse", date(2026, 12, 23), Icons.GENERAL_WASTE),
        ("Food waste", date(2026, 12, 24), Icons.ORGANIC),
    ]
    posts = [kw["json"] for method, _, kw in session.calls if method == "post"]
    assert [p.get("operationId") for p in posts] == [
        None,
        "search-id",
        "select-id",
        "next-id",
    ]
    assert posts[1]["changes"]["address"]["SearchString"]["value"] == "BN2 1AA"
    assert (
        posts[2]["changes"]["address"]["BHCCTheme.AddressTemp_SelectedAddress"]["value"]
        == "v1"
    )


def test_scoped_mendix_search_widget_uses_its_own_operation():
    session = CouncilSession()
    original_get = session.get

    def scoped_form(url, **kwargs):
        response = original_get(url, **kwargs)
        response.text = response.text.replace(
            'Template_SearchFieldOnly.actionButton3"',
            'Template_SearchFieldOnly.actionButton3.BartecCollective.Jobs_Get_Combined"',
        )
        return response

    session.get = scoped_form
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        result = brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    assert len(result) == 3
    operations = [
        kw["json"].get("operationId")
        for method, _, kw in session.calls
        if method == "post"
    ]
    assert operations == [None, "search-id", "select-id", "next-id"]


@pytest.mark.parametrize(
    "replacement",
    [
        "Template_SearchFieldOnly.actionButton30",
        "Template_SearchFieldOnly.actionButton3.Unrelated.OtherPage",
    ],
)
def test_search_widget_rejects_unrelated_prefix_or_page(replacement):
    session = CouncilSession()
    original_get = session.get

    def other_form(url, **kwargs):
        response = original_get(url, **kwargs)
        response.text = response.text.replace(
            "Template_SearchFieldOnly.actionButton3", replacement
        )
        return response

    session.get = other_form
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="Missing Mendix operation"):
            brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    assert [
        kw["json"].get("operationId")
        for method, _, kw in session.calls
        if method == "post"
    ] == [None]


def test_postcode_must_match_exactly_not_prefix():
    session = CouncilSession()
    session.search_postcode = "BN2 1AAA"
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="postcode did not match"):
            brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    assert not any(
        kw["json"].get("operationId") == "select-id"
        for method, _, kw in session.calls
        if method == "post"
    )


@pytest.mark.parametrize(
    "uprn", ["0", "000000000000", "١١١١١١١١", "111١1111", " 11111111 "]
)
def test_invalid_requested_uprn_is_rejected_before_network(uprn):
    session = CouncilSession()
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="positive ASCII"):
            brighton_hove_gov_uk.Source("BN2 1AA", uprn)
    assert session.calls == []


@pytest.mark.parametrize("uprn", [None, "0", "000000000000", "١١١١١١١١"])
def test_invalid_address_uprn_cannot_bind(uprn):
    session = CouncilSession()
    session.search_results[0] = ("v1", "11 West Drive, Brighton, BN2 1AA", uprn)
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(brighton_hove_gov_uk.SourceArgumentNotFound):
            brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()


@pytest.mark.parametrize("uprn", [None, "0", "000000000000", "١١١١١١١١"])
def test_invalid_collection_row_uprn_fails_fetch(uprn):
    session = CouncilSession()
    session.results[0] = ("Recycling", "22/12/2026, 08:00", "Not Started", uprn, "101")
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="another UPRN"):
            brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()


def test_address_selection_transmits_search_results_to_mendix():
    session = CouncilSession()
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    selection = [kw["json"] for method, _, kw in session.calls if method == "post"][2]
    assert selection["changes"]["v1"]["uprn"]["value"] == "000011111111"
    assert any(obj["guid"] == "v2" for obj in selection["objects"])


def test_transient_backend_message_retries_next_collections_once():
    session = CouncilSession()
    session.transient_next = True
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        result = brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    assert len(result) == 3
    assert (
        sum(
            kw["json"].get("operationId") == "next-id"
            for method, _, kw in session.calls
            if method == "post"
        )
        == 2
    )


def test_repeated_backend_error_with_rows_fails_after_one_retry():
    session = CouncilSession()
    session.transient_next = True
    session.error_with_rows = True
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="trouble connecting"):
            brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    assert (
        sum(
            kw["json"].get("operationId") == "next-id"
            for method, _, kw in session.calls
            if method == "post"
        )
        == 2
    )


def test_missing_refuse_is_not_silent_partial_success():
    session = CouncilSession()
    session.results = [r for r in session.results if r[0] != "Refuse"]
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="Refuse"):
            brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()


def test_mismatched_service_uprn_fails_instead_of_misattributing_collection():
    session = CouncilSession()
    session.results[1] = (
        "Refuse",
        "23/12/2026, 08:00",
        "Not Started",
        "99999999",
        "102",
    )
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="another UPRN"):
            brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()


def test_past_flagged_future_job_is_not_returned():
    session = CouncilSession()
    session.is_past[2] = True
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        result = brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    assert [entry.type for entry in result] == ["Recycling", "Refuse"]


def test_old_dated_open_job_is_not_returned():
    session = CouncilSession()
    session.results[2] = (
        "Food waste",
        "09/10/2026, 08:00",
        "Not Started",
        "11111111",
        "103",
    )
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        result = brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    assert [entry.type for entry in result] == ["Recycling", "Refuse"]


@pytest.mark.parametrize("invalid", [None, "false", 0, 1])
def test_malformed_is_past_fails_fetch(invalid):
    session = CouncilSession()
    session.is_past[2] = invalid
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="IsPast"):
            brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()


def test_today_in_london_survives_utc_previous_day_at_dst_boundary(council_clock):
    council_clock.utc_now = datetime(2026, 10, 24, 23, 30, tzinfo=timezone.utc)
    session = CouncilSession()
    session.results[2] = (
        "Food waste",
        "25/10/2026, 00:30",
        "Not Started",
        "11111111",
        "103",
    )
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        result = brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
    assert ("Food waste", date(2026, 10, 25)) in [
        (entry.type, entry.date) for entry in result
    ]


def test_unparseable_date_fails_instead_of_extrapolating_a_weekday():
    session = CouncilSession()
    session.results[1] = ("Refuse", "Tuesday", "Not Started", "11111111", "102")
    with patch.object(brighton_hove_gov_uk.requests, "Session", return_value=session):
        with pytest.raises(ValueError, match="time data"):
            brighton_hove_gov_uk.Source("BN2 1AA", "000011111111").fetch()
