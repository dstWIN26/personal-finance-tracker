"""ECB FX-rate parsing (offline — no network)."""
from backend.integrations import market


ECB_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
                 xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time="2026-06-16">
      <Cube currency="USD" rate="1.0856"/>
      <Cube currency="GBP" rate="0.8534"/>
      <Cube currency="CHF" rate="0.9512"/>
    </Cube>
  </Cube>
</gesmes:Envelope>"""


def test_parse_ecb_fx_extracts_rates_and_date():
    out = market._parse_ecb_fx(ECB_SAMPLE)
    assert out["base"] == "EUR"
    assert out["date"] == "2026-06-16"
    assert out["rates"]["EUR"] == 1.0          # base always present
    assert out["rates"]["USD"] == 1.0856
    assert out["rates"]["GBP"] == 0.8534
    assert out["rates"]["CHF"] == 0.9512


def test_parse_ecb_fx_tolerates_garbage_rate():
    bad = ECB_SAMPLE.replace('rate="1.0856"', 'rate="n/a"')
    out = market._parse_ecb_fx(bad)
    assert "USD" not in out["rates"]           # unparseable skipped, others fine
    assert out["rates"]["GBP"] == 0.8534
