# Test fixtures

The `synthetic_*.json` files are **hand-written**. They follow the field names and
value styles Met Éireann documents for the observations feed (numbers as strings,
`"-"` for a missing gust, `date` + `reportTime` in local time). They also include
awkward cases on purpose: a `"Calm"` wind, `"n/a"` pressure, an empty rainfall value,
and a record with no `windDirection`.

To check the parser against the real feed, capture live responses from a machine
that can reach `prodapi.metweb.ie`:

    flask --app met_api probe --save tests/fixtures/real

That writes one file per station and feed, and prints how each one parsed.
