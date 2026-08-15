from lxml import etree

def _text(root,xpaths):
    for xp in xpaths:
        try:
            r=root.xpath(xp)
            if r:
                v=r[0]
                return str(v if isinstance(v,str) else (v.text or "")).strip()
        except Exception:
            continue
    return None

def parse_xml_invoice(path):
    root=etree.parse(str(path)).getroot()
    fmt="CII" if "CrossIndustryInvoice" in str(root.tag) else "UBL"
    def f(v):
        try:return float(v or 0)
        except:return 0.0
    return {
      "format":fmt,"direction":"purchase",
      "invoice_number":_text(root,['//*[local-name()="ID"]/text()','//*[local-name()="ExchangedDocument"]/*[local-name()="ID"]/text()']),
      "issue_date":_text(root,['//*[local-name()="IssueDate"]/text()','//*[local-name()="IssueDateTime"]//*[local-name()="DateTimeString"]/text()']),
      "due_date":_text(root,['//*[local-name()="DueDate"]/text()','//*[local-name()="SpecifiedTradePaymentTerms"]//*[local-name()="DateTimeString"]/text()']),
      "supplier":{"name":_text(root,['//*[local-name()="AccountingSupplierParty"]//*[local-name()="RegistrationName"]/text()','//*[local-name()="SellerTradeParty"]//*[local-name()="Name"]/text()']) or ""},
      "customer":{"name":_text(root,['//*[local-name()="AccountingCustomerParty"]//*[local-name()="RegistrationName"]/text()','//*[local-name()="BuyerTradeParty"]//*[local-name()="Name"]/text()']) or ""},
      "net_amount":f(_text(root,['//*[local-name()="TaxExclusiveAmount"]/text()','//*[local-name()="TaxBasisTotalAmount"]/text()'])),
      "vat_amount":f(_text(root,['//*[local-name()="TaxAmount"]/text()','//*[local-name()="TaxTotalAmount"]/text()'])),
      "gross_amount":f(_text(root,['//*[local-name()="PayableAmount"]/text()','//*[local-name()="GrandTotalAmount"]/text()'])),
      "currency":_text(root,['//*[local-name()="DocumentCurrencyCode"]/text()','//*[local-name()="InvoiceCurrencyCode"]/text()']) or "EUR",
      "lines":[]
    }
