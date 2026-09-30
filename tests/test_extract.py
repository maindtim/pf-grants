import csv
import io
from pathlib import Path

import pytest

from pfgrants.extract import NotA990PF, parse_990pf
from pfgrants.query import (filter_rows, grant_rows, read_csv, summarize_by_foundation,
                            write_csv, GRANT_COLUMNS)

FIXTURES = Path(__file__).parent / "fixtures"


def _xml(body_supp: str = "", return_type: str = "990PF", extra_pf: str = "") -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Return xmlns="http://www.irs.gov/efile" returnVersion="2024v5.0">
  <ReturnHeader>
    <ReturnTs>2026-05-12T10:11:12-04:00</ReturnTs>
    <TaxPeriodEndDt>2025-12-31</TaxPeriodEndDt>
    <ReturnTypeCd>{return_type}</ReturnTypeCd>
    <TaxYr>2025</TaxYr>
    <Filer>
      <EIN>123456789</EIN>
      <BusinessName><BusinessNameLine1Txt>EXAMPLE FAMILY FOUNDATION</BusinessNameLine1Txt></BusinessName>
      <PhoneNum>5555550100</PhoneNum>
      <USAddress>
        <AddressLine1Txt>1 MAIN ST</AddressLine1Txt>
        <CityNm>WINDER</CityNm>
        <StateAbbreviationCd>GA</StateAbbreviationCd>
        <ZIPCd>30680</ZIPCd>
      </USAddress>
    </Filer>
  </ReturnHeader>
  <ReturnData>
    <IRS990PF>
      <FMVAssetsEOYAmt>2500000</FMVAssetsEOYAmt>
      <AnalysisOfRevenueAndExpenses>
        <ContriPaidRevAndExpnssAmt>120000</ContriPaidRevAndExpnssAmt>
        <ContriPaidDsbrsChrtblAmt>118000</ContriPaidDsbrsChrtblAmt>
      </AnalysisOfRevenueAndExpenses>
      <StatementsRegardingActyGrp>
        <WebsiteAddressTxt>WWW.EXAMPLE.ORG</WebsiteAddressTxt>
      </StatementsRegardingActyGrp>
      {extra_pf}
      <SupplementaryInformationGrp>
        {body_supp}
      </SupplementaryInformationGrp>
    </IRS990PF>
  </ReturnData>
</Return>
""".encode()


SUPP_OPEN = """
<ApplicationSubmissionInfoGrp>
  <RecipientPersonNm>JANE DOE, TRUSTEE</RecipientPersonNm>
  <RecipientUSAddress>
    <AddressLine1Txt>PO BOX 9</AddressLine1Txt><CityNm>WINDER</CityNm>
    <StateAbbreviationCd>GA</StateAbbreviationCd><ZIPCd>30680</ZIPCd>
  </RecipientUSAddress>
  <FormAndInfoAndMaterialsTxt>LETTER OF REQUEST WITH BUDGET</FormAndInfoAndMaterialsTxt>
  <SubmissionDeadlinesTxt>SEPTEMBER 30</SubmissionDeadlinesTxt>
  <RestrictionsOnAwardsTxt>BARROW COUNTY, GA ONLY</RestrictionsOnAwardsTxt>
</ApplicationSubmissionInfoGrp>
<GrantOrContributionPdDurYrGrp>
  <RecipientBusinessName><BusinessNameLine1Txt>ADULT LITERACY BARROW INC</BusinessNameLine1Txt></RecipientBusinessName>
  <RecipientUSAddress><CityNm>WINDER</CityNm><StateAbbreviationCd>GA</StateAbbreviationCd><ZIPCd>30680</ZIPCd></RecipientUSAddress>
  <RecipientRelationshipTxt>NONE</RecipientRelationshipTxt>
  <RecipientFoundationStatusTxt>PC</RecipientFoundationStatusTxt>
  <GrantOrContributionPurposeTxt>ADULT LITERACY TUTORING</GrantOrContributionPurposeTxt>
  <Amt>5000</Amt>
</GrantOrContributionPdDurYrGrp>
<GrantOrContributionPdDurYrGrp>
  <RecipientPersonNm>JOHN SMITH</RecipientPersonNm>
  <RecipientForeignAddress><CityNm>TORONTO</CityNm><ProvinceOrStateNm>ON</ProvinceOrStateNm><CountryCd>CA</CountryCd></RecipientForeignAddress>
  <GrantOrContributionPurposeTxt>SCHOLARSHIP</GrantOrContributionPurposeTxt>
  <Amt>1500</Amt>
</GrantOrContributionPdDurYrGrp>
<TotalGrantOrContriPdDurYrAmt>6500</TotalGrantOrContriPdDurYrAmt>
<GrantOrContriApprvForFutGrp>
  <RecipientBusinessName><BusinessNameLine1Txt>WINDER FOOD PANTRY</BusinessNameLine1Txt></RecipientBusinessName>
  <RecipientUSAddress><CityNm>WINDER</CityNm><StateAbbreviationCd>GA</StateAbbreviationCd></RecipientUSAddress>
  <GrantOrContributionPurposeTxt>FOOD PROGRAM</GrantOrContributionPurposeTxt>
  <Amt>2000</Amt>
</GrantOrContriApprvForFutGrp>
<TotalGrantOrContriApprvFutAmt>2000</TotalGrantOrContriApprvFutAmt>
"""

SUPP_PRESELECTED = """
<OnlyContriToPreselectedInd>X</OnlyContriToPreselectedInd>
<GrantOrContributionPdDurYrGrp>
  <RecipientBusinessName><BusinessNameLine1Txt>SOME UNIVERSITY</BusinessNameLine1Txt></RecipientBusinessName>
  <RecipientUSAddress><CityNm>ATHENS</CityNm><StateAbbreviationCd>GA</StateAbbreviationCd></RecipientUSAddress>
  <GrantOrContributionPurposeTxt>GENERAL SUPPORT</GrantOrContributionPurposeTxt>
  <Amt>100000</Amt>
</GrantOrContributionPdDurYrGrp>
"""


def test_parses_header_and_totals():
    f = parse_990pf(_xml(SUPP_OPEN))
    assert f.ein == "123456789"
    assert f.name == "EXAMPLE FAMILY FOUNDATION"
    assert (f.city, f.state, f.zip) == ("WINDER", "GA", "30680")
    assert f.tax_year == "2025" and f.tax_period_end == "2025-12-31"
    assert f.fmv_assets_eoy == 2500000
    assert f.contributions_paid == 118000  # column (d) preferred over (a)
    assert f.website == "WWW.EXAMPLE.ORG"
    assert f.phone == "5555550100"
    assert f.total_grants_paid_listed == 6500
    assert f.total_grants_future_listed == 2000


def test_parses_grants_paid_and_future():
    f = parse_990pf(_xml(SUPP_OPEN))
    assert len(f.grants_paid) == 2 and len(f.grants_future) == 1
    g = f.grants_paid[0]
    assert g.recipient == "ADULT LITERACY BARROW INC"
    assert (g.recipient_city, g.recipient_state, g.recipient_zip, g.recipient_country) == ("WINDER", "GA", "30680", "US")
    assert g.purpose == "ADULT LITERACY TUTORING" and g.amount == 5000
    assert g.relationship == "NONE" and g.foundation_status == "PC"
    person = f.grants_paid[1]
    assert person.recipient == "JOHN SMITH"
    assert person.recipient_country == "CA" and person.recipient_state == "ON"
    fut = f.grants_future[0]
    assert fut.kind == "future" and fut.recipient == "WINDER FOOD PANTRY" and fut.amount == 2000


def test_application_info_when_open():
    f = parse_990pf(_xml(SUPP_OPEN))
    assert f.preselected_only is False
    assert f.application_contact == "JANE DOE, TRUSTEE"
    assert f.application_address == "PO BOX 9, WINDER, GA, 30680"
    assert f.application_form_info == "LETTER OF REQUEST WITH BUDGET"
    assert f.application_deadlines == "SEPTEMBER 30"
    assert f.application_restrictions == "BARROW COUNTY, GA ONLY"


def test_preselected_flag():
    f = parse_990pf(_xml(SUPP_PRESELECTED))
    assert f.preselected_only is True
    assert f.application_contact == ""
    assert len(f.grants) == 1


def test_missing_supplementary_group_gives_no_grants():
    f = parse_990pf(_xml(""))
    assert f.grants == [] and f.total_grants_paid_listed is None


def test_rejects_other_return_types():
    with pytest.raises(NotA990PF):
        parse_990pf(_xml(SUPP_OPEN, return_type="990EZ"))


def test_amount_tolerates_blank_and_decimal():
    body = SUPP_PRESELECTED.replace("<Amt>100000</Amt>", "<Amt>100000.00</Amt>")
    assert parse_990pf(_xml(body)).grants[0].amount == 100000
    body = SUPP_PRESELECTED.replace("<Amt>100000</Amt>", "<Amt></Amt>")
    assert parse_990pf(_xml(body)).grants[0].amount is None


def test_rows_filter_and_csv_roundtrip(tmp_path):
    f_open = parse_990pf(_xml(SUPP_OPEN))
    f_pre = parse_990pf(_xml(SUPP_PRESELECTED))
    rows = list(grant_rows([f_open, f_pre]))
    assert len(rows) == 4
    assert set(rows[0]) >= set(GRANT_COLUMNS) - {"grant_kind"} | {"grant_kind"}
    assert rows[0]["source_url"] == "https://projects.propublica.org/nonprofits/organizations/123456789"

    lit = list(filter_rows(rows, keywords=["literacy"]))
    assert [r["recipient"] for r in lit] == ["ADULT LITERACY BARROW INC"]
    ga_paid = list(filter_rows(rows, recipient_state="ga", kind="paid"))
    assert {r["recipient"] for r in ga_paid} == {"ADULT LITERACY BARROW INC", "SOME UNIVERSITY"}
    open_only = list(filter_rows(rows, exclude_preselected=True))
    assert all(r["preselected_only"] == "no" for r in open_only) and len(open_only) == 3
    big = list(filter_rows(rows, min_amount=50000))
    assert [r["recipient"] for r in big] == ["SOME UNIVERSITY"]

    dest = tmp_path / "grants.csv"
    assert write_csv(rows, dest) == 4
    back = read_csv(dest)
    assert back[0]["amount"] == 5000 and back[0]["fmv_assets_eoy"] == 2500000
    assert list(csv.DictReader(io.StringIO(dest.read_text(encoding="utf-8")))) [0]["preselected_only"] == "no"


def test_summary_groups_by_foundation():
    rows = list(grant_rows([parse_990pf(_xml(SUPP_OPEN)), parse_990pf(_xml(SUPP_PRESELECTED))]))
    summ = summarize_by_foundation(rows)
    # both fixtures share the EIN, so they collapse into one line
    assert len(summ) == 1
    assert summ[0]["matching_grants"] == 4 and summ[0]["matching_amount"] == 108500
    assert "ADULT LITERACY BARROW INC" in summ[0]["recipients"]


@pytest.mark.skipif(not list(FIXTURES.glob("*.xml")), reason="no real IRS fixture checked in")
def test_parallel_worker_matches_sequential():
    # exercises the per-file worker used by rows_from_dir_parallel without spawning processes
    from pfgrants.query import rows_from_dir, _rows_for_file
    seq = list(rows_from_dir(FIXTURES))
    par = []
    for path in sorted(FIXTURES.glob("*.xml")):
        rows, err = _rows_for_file(str(path))
        assert err is None
        par.extend(rows)
    assert seq == par and len(seq) > 0
    rows, err = _rows_for_file(str(FIXTURES / "does-not-exist.xml"))
    assert rows == [] and err


@pytest.mark.skipif(not list(FIXTURES.glob("*.xml")), reason="no real IRS fixture checked in")
def test_real_irs_fixture_parses():
    for path in FIXTURES.glob("*.xml"):
        f = parse_990pf(path)
        assert f.ein.isdigit() and len(f.ein) == 9
        assert f.name
        assert f.object_id == path.name.split("_")[0]
        for g in f.grants:
            assert g.kind in ("paid", "future")
