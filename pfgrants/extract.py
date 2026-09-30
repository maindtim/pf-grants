"""Parse an IRS e-filed Form 990-PF XML and pull out the grants list.

Every private foundation that e-files a 990-PF must list, in the
"Supplementary Information" part (Part XIV on the 2024 form; it was Part XV on
older forms), each grant or contribution paid during the year (recipient,
address, relationship, foundation status, purpose, amount) and those approved
for future payment. It also states whether it only gives to pre-selected
organizations (line 2) and, if not, how to apply. This module turns that into
plain dicts.

Only public data. Only what the foundation itself filed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterator

from lxml import etree

NS = {"e": "http://www.irs.gov/efile"}


def _text(node, path: str) -> str:
    """First text match for *path* under *node*, stripped; '' if absent."""
    if node is None:
        return ""
    found = node.find(path, NS)
    if found is None or found.text is None:
        return ""
    return " ".join(found.text.split())


def _amount(node, path: str) -> int | None:
    t = _text(node, path)
    if t == "":
        return None
    try:
        return int(float(t))
    except ValueError:
        return None


def _name(node, person_path: str, business_path: str) -> str:
    person = _text(node, person_path)
    if person:
        return person
    biz = node.find(business_path, NS) if node is not None else None
    if biz is None:
        return ""
    parts = [_text(biz, "e:BusinessNameLine1Txt"), _text(biz, "e:BusinessNameLine2Txt")]
    return " ".join(p for p in parts if p)


def _address(node, us_path: str, foreign_path: str) -> dict:
    us = node.find(us_path, NS) if node is not None else None
    if us is not None:
        return {
            "address": " ".join(p for p in (_text(us, "e:AddressLine1Txt"), _text(us, "e:AddressLine2Txt")) if p),
            "city": _text(us, "e:CityNm"),
            "state": _text(us, "e:StateAbbreviationCd"),
            "zip": _text(us, "e:ZIPCd"),
            "country": "US",
        }
    fo = node.find(foreign_path, NS) if node is not None else None
    if fo is not None:
        return {
            "address": " ".join(p for p in (_text(fo, "e:AddressLine1Txt"), _text(fo, "e:AddressLine2Txt")) if p),
            "city": _text(fo, "e:CityNm"),
            "state": _text(fo, "e:ProvinceOrStateNm"),
            "zip": _text(fo, "e:ForeignPostalCd"),
            "country": _text(fo, "e:CountryCd"),
        }
    return {"address": "", "city": "", "state": "", "zip": "", "country": ""}


@dataclass
class Grant:
    kind: str                       # "paid" (Supplementary Information line 3a) or "future" (line 3b)
    recipient: str
    recipient_city: str
    recipient_state: str
    recipient_zip: str
    recipient_country: str
    relationship: str
    foundation_status: str
    purpose: str
    amount: int | None


@dataclass
class Foundation:
    object_id: str
    ein: str
    name: str
    city: str
    state: str
    zip: str
    tax_year: str
    tax_period_end: str
    return_ts: str
    website: str
    phone: str
    fmv_assets_eoy: int | None              # heading, item I (fair market value of all assets at end of year)
    contributions_paid: int | None          # Part I line 25, column (d)
    total_grants_paid_listed: int | None    # Supplementary Information, total of 3a
    total_grants_future_listed: int | None  # Supplementary Information, total of 3b
    preselected_only: bool                  # Supplementary Information, line 2 checkbox
    application_contact: str
    application_address: str
    application_form_info: str
    application_deadlines: str
    application_restrictions: str
    grants: list[Grant] = field(default_factory=list)

    @property
    def grants_paid(self) -> list[Grant]:
        return [g for g in self.grants if g.kind == "paid"]

    @property
    def grants_future(self) -> list[Grant]:
        return [g for g in self.grants if g.kind == "future"]

    def to_dict(self, with_grants: bool = True) -> dict:
        d = asdict(self)
        if not with_grants:
            d.pop("grants")
        return d


class NotA990PF(ValueError):
    """Raised when the XML is a valid e-file return but not a Form 990-PF."""


def _grants(group_parent, tag: str, kind: str) -> Iterator[Grant]:
    if group_parent is None:
        return
    for g in group_parent.findall(f"e:{tag}", NS):
        addr = _address(g, "e:RecipientUSAddress", "e:RecipientForeignAddress")
        yield Grant(
            kind=kind,
            recipient=_name(g, "e:RecipientPersonNm", "e:RecipientBusinessName"),
            recipient_city=addr["city"],
            recipient_state=addr["state"],
            recipient_zip=addr["zip"],
            recipient_country=addr["country"],
            relationship=_text(g, "e:RecipientRelationshipTxt"),
            foundation_status=_text(g, "e:RecipientFoundationStatusTxt"),
            purpose=_text(g, "e:GrantOrContributionPurposeTxt"),
            amount=_amount(g, "e:Amt"),
        )


def parse_990pf(source: str | Path | bytes) -> Foundation:
    """Parse one e-filed 990-PF. *source* is a path or raw bytes.

    Raises NotA990PF if the return type is something else (990, 990-EZ, ...).
    """
    if isinstance(source, (str, Path)):
        tree = etree.parse(str(source))
        root = tree.getroot()
        object_id = Path(source).name.split("_")[0]
    else:
        root = etree.fromstring(source)
        object_id = ""

    header = root.find("e:ReturnHeader", NS)
    return_type = _text(header, "e:ReturnTypeCd")
    pf = root.find("e:ReturnData/e:IRS990PF", NS)
    if return_type != "990PF" or pf is None:
        raise NotA990PF(f"return type is {return_type or 'unknown'}, not 990PF")

    filer = header.find("e:Filer", NS)
    filer_addr = _address(filer, "e:USAddress", "e:ForeignAddress")
    supp = pf.find("e:SupplementaryInformationGrp", NS)
    app = supp.find("e:ApplicationSubmissionInfoGrp", NS) if supp is not None else None
    app_addr = _address(app, "e:RecipientUSAddress", "e:RecipientForeignAddress") if app is not None else None

    preselected = _text(supp, "e:OnlyContriToPreselectedInd").upper() == "X" if supp is not None else False

    fdn = Foundation(
        object_id=object_id,
        ein=_text(filer, "e:EIN"),
        name=_name(filer, "e:__none__", "e:BusinessName"),
        city=filer_addr["city"],
        state=filer_addr["state"],
        zip=filer_addr["zip"],
        tax_year=_text(header, "e:TaxYr"),
        tax_period_end=_text(header, "e:TaxPeriodEndDt"),
        return_ts=_text(header, "e:ReturnTs"),
        website=_text(pf, ".//e:WebsiteAddressTxt"),
        phone=_text(filer, "e:PhoneNum") or _text(pf, ".//e:PhoneNum"),
        fmv_assets_eoy=_amount(pf, "e:FMVAssetsEOYAmt"),
        contributions_paid=_amount(pf, "e:AnalysisOfRevenueAndExpenses/e:ContriPaidDsbrsChrtblAmt")
        if _amount(pf, "e:AnalysisOfRevenueAndExpenses/e:ContriPaidDsbrsChrtblAmt") is not None
        else _amount(pf, "e:AnalysisOfRevenueAndExpenses/e:ContriPaidRevAndExpnssAmt"),
        total_grants_paid_listed=_amount(supp, "e:TotalGrantOrContriPdDurYrAmt"),
        total_grants_future_listed=_amount(supp, "e:TotalGrantOrContriApprvFutAmt"),
        preselected_only=preselected,
        application_contact=_name(app, "e:RecipientPersonNm", "e:RecipientBusinessName") if app is not None else "",
        application_address=(", ".join(p for p in (app_addr["address"], app_addr["city"], app_addr["state"], app_addr["zip"]) if p)
                             if app_addr else ""),
        application_form_info=_text(app, "e:FormAndInfoAndMaterialsTxt") if app is not None else "",
        application_deadlines=_text(app, "e:SubmissionDeadlinesTxt") if app is not None else "",
        application_restrictions=_text(app, "e:RestrictionsOnAwardsTxt") if app is not None else "",
    )
    fdn.grants.extend(_grants(supp, "GrantOrContributionPdDurYrGrp", "paid"))
    fdn.grants.extend(_grants(supp, "GrantOrContriApprvForFutGrp", "future"))
    return fdn


def iter_990pf(xml_dir: str | Path, on_error=None) -> Iterator[Foundation]:
    """Yield a Foundation for every parseable 990-PF XML in *xml_dir*."""
    for path in sorted(Path(xml_dir).glob("*.xml")):
        try:
            yield parse_990pf(path)
        except (NotA990PF, etree.XMLSyntaxError) as exc:
            if on_error:
                on_error(path, exc)
