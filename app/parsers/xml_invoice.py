from datetime import datetime

from lxml import etree

from app.services.company import determine_invoice_direction, get_active_company


def _element(root, xpaths):
    for xpath in xpaths:
        try:
            result = root.xpath(xpath)
            if result:
                return result[0]
        except (etree.XPathError, TypeError):
            continue
    return None


def _text(root, xpaths):
    element = _element(root, xpaths)
    if element is None:
        return None
    value = element if isinstance(element, str) else element.text
    return str(value or "").strip() or None


def _number(value):
    try:
        return float(str(value or "0").replace(" ", "").replace(",", "."))
    except ValueError:
        return 0.0


def _normalise_date(element):
    if element is None:
        return None
    value = str(element.text or "").strip()
    format_code = element.get("format") or element.get("formatCode")
    formats = {
        "102": "%Y%m%d",
        "203": "%Y%m%d%H%M",
        "610": "%Y%m",
    }
    if format_code in formats:
        try:
            parsed = datetime.strptime(value, formats[format_code])
            return parsed.date().isoformat() if format_code != "610" else parsed.strftime("%Y-%m")
        except ValueError:
            return None
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _address(party):
    if party is None:
        return ""
    parts = []
    for xpath in (
        './/*[local-name()="PostalAddress"]/*[local-name()="StreetName"]/text()',
        './/*[local-name()="PostalAddress"]/*[local-name()="AdditionalStreetName"]/text()',
        './/*[local-name()="PostalAddress"]/*[local-name()="PostalZone"]/text()',
        './/*[local-name()="PostalAddress"]/*[local-name()="CityName"]/text()',
        './/*[local-name()="PostalTradeAddress"]/*[local-name()="LineOne"]/text()',
        './/*[local-name()="PostalTradeAddress"]/*[local-name()="PostcodeCode"]/text()',
        './/*[local-name()="PostalTradeAddress"]/*[local-name()="CityName"]/text()',
    ):
        value = _text(party, [xpath])
        if value and value not in parts:
            parts.append(value)
    return ", ".join(parts)


def _ubl_party(root, role):
    party = _element(root, [f'./*[local-name()="{role}"]/*[local-name()="Party"]'])
    if party is None:
        return {"name": ""}
    name = _text(party, [
        './*[local-name()="PartyName"]/*[local-name()="Name"]/text()',
        './*[local-name()="PartyLegalEntity"]/*[local-name()="RegistrationName"]/text()',
    ]) or ""
    vat_id = _text(party, [
        './*[local-name()="PartyTaxScheme"]/*[local-name()="CompanyID"]/text()',
    ])
    legal_id = _text(party, [
        './*[local-name()="PartyLegalEntity"]/*[local-name()="CompanyID"]/text()',
        './*[local-name()="EndpointID"]/text()',
    ])
    result = {"name": name, "address": _address(party)}
    if vat_id:
        result.update({"vat_id": vat_id, "vat_number": vat_id})
    if legal_id:
        result["legal_id"] = legal_id
    return result


def _cii_party(root, role):
    party = _element(root, [f'.//*[local-name()="{role}"]'])
    if party is None:
        return {"name": ""}
    name = _text(party, ['./*[local-name()="Name"]/text()']) or ""
    vat_id = _text(party, [
        './/*[local-name()="SpecifiedTaxRegistration"]/*[local-name()="ID"][@schemeID="VA"]/text()',
        './/*[local-name()="SpecifiedTaxRegistration"]/*[local-name()="ID"]/text()',
    ])
    legal_id = _text(party, ['./*[local-name()="ID"]/text()', './/*[local-name()="GlobalID"]/text()'])
    result = {"name": name, "address": _address(party)}
    if vat_id:
        result.update({"vat_id": vat_id, "vat_number": vat_id})
    if legal_id:
        result["legal_id"] = legal_id
    return result


def _ubl_lines(root):
    lines = []
    for node in root.xpath('./*[local-name()="InvoiceLine"] | ./*[local-name()="CreditNoteLine"]'):
        description = _text(node, [
            './*[local-name()="Item"]/*[local-name()="Description"]/text()',
            './*[local-name()="Item"]/*[local-name()="Name"]/text()',
        ])
        quantity = _number(_text(node, [
            './*[local-name()="InvoicedQuantity"]/text()', './*[local-name()="CreditedQuantity"]/text()'
        ]))
        unit_price = _number(_text(node, ['./*[local-name()="Price"]/*[local-name()="PriceAmount"]/text()']))
        line_total = _number(_text(node, ['./*[local-name()="LineExtensionAmount"]/text()']))
        if description and quantity and line_total:
            lines.append({"description": description, "quantity": quantity,
                          "unit_price_net": unit_price, "line_total_net": line_total})
    return lines


def _cii_lines(root):
    lines = []
    for node in root.xpath('.//*[local-name()="IncludedSupplyChainTradeLineItem"]'):
        description = _text(node, [
            './/*[local-name()="SpecifiedTradeProduct"]/*[local-name()="Name"]/text()',
            './/*[local-name()="SpecifiedTradeProduct"]/*[local-name()="Description"]/text()',
        ])
        quantity = _number(_text(node, ['.//*[local-name()="BilledQuantity"]/text()']))
        unit_price = _number(_text(node, [
            './/*[local-name()="NetPriceProductTradePrice"]/*[local-name()="ChargeAmount"]/text()',
            './/*[local-name()="GrossPriceProductTradePrice"]/*[local-name()="ChargeAmount"]/text()',
        ]))
        line_total = _number(_text(node, [
            './/*[local-name()="SpecifiedTradeSettlementLineMonetarySummation"]/*[local-name()="LineTotalAmount"]/text()'
        ]))
        if description and quantity and line_total:
            lines.append({"description": description, "quantity": quantity,
                          "unit_price_net": unit_price, "line_total_net": line_total})
    return lines


def parse_xml_invoice(path):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
    root = etree.parse(str(path), parser).getroot()
    local_tag = etree.QName(root).localname
    if local_tag == "CrossIndustryInvoice":
        invoice_format = "CII"
    elif local_tag in {"Invoice", "CreditNote"}:
        invoice_format = "UBL"
    else:
        raise ValueError(f"XML de facture non reconnu: {local_tag}")

    if invoice_format == "UBL":
        supplier = _ubl_party(root, "AccountingSupplierParty")
        customer = _ubl_party(root, "AccountingCustomerParty")
        invoice_number = _text(root, ['./*[local-name()="ID"]/text()'])
        issue_element = _element(root, ['./*[local-name()="IssueDate"]'])
        due_element = _element(root, ['./*[local-name()="DueDate"]'])
        net_amount = _number(_text(root, ['.//*[local-name()="LegalMonetaryTotal"]/*[local-name()="TaxExclusiveAmount"]/text()']))
        vat_amount = _number(_text(root, ['./*[local-name()="TaxTotal"]/*[local-name()="TaxAmount"]/text()']))
        gross_amount = _number(_text(root, ['./*[local-name()="LegalMonetaryTotal"]/*[local-name()="PayableAmount"]/text()']))
        currency = _text(root, ['./*[local-name()="DocumentCurrencyCode"]/text()']) or "EUR"
        lines = _ubl_lines(root)
    else:
        supplier = _cii_party(root, "SellerTradeParty")
        customer = _cii_party(root, "BuyerTradeParty")
        invoice_number = _text(root, ['.//*[local-name()="ExchangedDocument"]/*[local-name()="ID"]/text()'])
        issue_element = _element(root, ['.//*[local-name()="ExchangedDocument"]/*[local-name()="IssueDateTime"]//*[local-name()="DateTimeString"]'])
        due_element = _element(root, ['.//*[local-name()="SpecifiedTradePaymentTerms"]//*[local-name()="DateTimeString"]'])
        net_amount = _number(_text(root, ['.//*[local-name()="SpecifiedTradeSettlementHeaderMonetarySummation"]/*[local-name()="TaxBasisTotalAmount"]/text()']))
        vat_amount = _number(_text(root, ['.//*[local-name()="SpecifiedTradeSettlementHeaderMonetarySummation"]/*[local-name()="TaxTotalAmount"]/text()']))
        gross_amount = _number(_text(root, ['.//*[local-name()="SpecifiedTradeSettlementHeaderMonetarySummation"]/*[local-name()="GrandTotalAmount"]/text()']))
        currency = _text(root, ['.//*[local-name()="ApplicableHeaderTradeSettlement"]/*[local-name()="InvoiceCurrencyCode"]/text()']) or "EUR"
        lines = _cii_lines(root)

    company = get_active_company()
    direction, evidence, company_id = determine_invoice_direction(supplier, customer, company)
    document_type = "credit_note" if local_tag == "CreditNote" else "invoice"
    return {
        "format": invoice_format,
        "document_type": document_type,
        "direction": direction,
        "direction_evidence": evidence,
        "company_id": company_id,
        "invoice_number": invoice_number,
        "issue_date": _normalise_date(issue_element),
        "due_date": _normalise_date(due_element),
        "issuer": dict(supplier),
        "supplier": supplier,
        "customer": customer,
        "net_amount": net_amount,
        "vat_amount": vat_amount,
        "gross_amount": gross_amount,
        "currency": currency,
        "lines": lines,
        "lines_extraction_confidence": "structured" if lines else "none",
        "lines_extraction_method": "xml",
        "needs_manual_validation": direction == "unknown" or not bool(lines),
    }
