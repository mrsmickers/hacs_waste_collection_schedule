# Brighton & Hove City Council

Collection dates from the [council's bin, box and caddy checker](https://www.brighton-hove.gov.uk/rubbish-recycling-and-streets/bins-and-collections/check-your-bin-box-and-caddy-collection-days), using its public Mendix service. Dates are retrieved from the council for the selected address; the source does **not** extrapolate weekdays or assume that holiday collections follow a fixed schedule. The checker currently exposes a short window of recent and next dates, not a year-long calendar.

## Configuration via Home Assistant UI

In **Settings → Devices & services → Waste Collection Schedule → Add source**, choose **Brighton & Hove City Council** and enter your postcode and positive Unique Property Reference Number (UPRN) as 1–12 ASCII digits, without spaces. The UPRN must identify the address returned for that exact postcode (ignoring postcode spaces and case). [Find My Address](https://www.findmyaddress.co.uk/) can help locate a UPRN. Leading zeroes may be included or omitted. This source uses a fresh council session for each update and needs no Selenium/browser add-on.

## YAML example

```yaml
waste_collection_schedule:
  sources:
    - name: brighton_hove_gov_uk
      args:
        postcode: BN2 0GD
        uprn: "000022064770" # Public example: 11 West Drive
```

## Notes

- Service names include `Refuse`, `Recycling`, and (where provided) `Food waste`. A `Garden Waste` entry is shown **only if subscribed**; the council also returns illustrative garden dates for unsubscribed properties, which are suppressed. Only jobs not marked `IsPast`, not closed, and dated today or later in Europe/London are shown.
- No date or service is invented when the council is unavailable or its response is incomplete. A known transient upstream error is retried once; thereafter the fetch fails rather than silently presenting partial data.
- The source depends on the council's public Mendix form and microflows. A website redesign can require updating the source; monitor integration errors and validate against the council website.
