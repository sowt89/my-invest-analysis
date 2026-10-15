#!/usr/bin/env python3
"""Network-free tests of reporting cutoffs, original SEC interpretation and duplicates."""
import copy
import csv
import io
import unittest
import zipfile
from datetime import date, datetime
from zoneinfo import ZoneInfo
from sec_insider_purchases import (latest_cutoff, month_shift, parse_document,
                                   quarter_candidates, summarize, report_window)

XML = '''<ownershipDocument><documentType>4</documentType><periodOfReport>2026-09-19</periodOfReport>
<issuer><issuerCik>0000000001</issuerCik><issuerName>Example</issuerName><issuerTradingSymbol>TEST</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerCik>2</rptOwnerCik><rptOwnerName>Buyer</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>1</isOfficer><officerTitle>CEO</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>{rows}</nonDerivativeTable><footnotes><footnote id="F1">{note}</footnote></footnotes></ownershipDocument>'''
ROW = '''<nonDerivativeTransaction><securityTitle><value>{title}</value></securityTitle>
<transactionDate><value>2026-09-19</value></transactionDate><transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>100</value></transactionShares><transactionPricePerShare><value>{price}</value><footnoteId id="F1"/></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>{side}</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>500</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature></nonDerivativeTransaction>'''


def document(code='P', side='A', title='Class A Common Stock', price='10', note='Open market purchase', accession='a'):
    row = ROW.format(code=code, side=side, title=title, price=price)
    return parse_document(XML.format(rows=row, note=note), {'accession': accession, 'filed': '2026-09-21'}, 'https://www.sec.gov/example.xml')


def report(docs):
    return summarize(docs, date(2026, 3, 22), date(2026, 8, 22), date(2026, 9, 21))


class InsiderTests(unittest.TestCase):
    def test_cutoff_and_year_rollover(self):
        self.assertEqual(latest_cutoff(datetime(2026, 10, 21, 15, 16, tzinfo=ZoneInfo('Asia/Seoul'))), date(2026, 9, 21))
        self.assertEqual(latest_cutoff(datetime(2026, 10, 21, 15, 17, tzinfo=ZoneInfo('Asia/Seoul'))), date(2026, 10, 21))
        self.assertEqual(latest_cutoff(datetime(2026, 1, 1, tzinfo=ZoneInfo('Asia/Seoul'))), date(2025, 12, 21))
        self.assertEqual(month_shift(date(2026, 1, 21), -6), date(2025, 7, 21))

    def test_korean_cutoff_utc_and_sec_window(self):
        self.assertEqual(latest_cutoff(datetime(2026, 10, 21, 6, 17, tzinfo=ZoneInfo('UTC'))), date(2026, 10, 21))
        cutoff, start, first = report_window(date(2026, 9, 21))
        self.assertEqual((cutoff, start, first), (date(2026, 9, 20), date(2026, 3, 21), date(2026, 8, 21)))
        before = document();before['filed'] = '2026-09-20';before['transactions'][0]['date'] = '2026-08-21'
        after = document(accession='late')
        companies, _ = summarize([before, after], start, first, cutoff)
        self.assertEqual(len(companies[0]['transactions']), 1)
        self.assertEqual(companies[0]['value'], 1000)
        self.assertEqual(report_window(date(2027, 1, 21)), (date(2027, 1, 20), date(2026, 7, 21), date(2026, 12, 21)))

    def test_only_p_acquired_common_stock(self):
        for code in ['A', 'M', 'F', 'S']:
            self.assertFalse(document(code=code)['transactions'])
        self.assertFalse(document(side='D')['transactions'])
        self.assertFalse(document(title='Series A Perpetual Preferred Stock')['transactions'])
        c, review = report([document()])
        self.assertEqual(c[0]['value'], 1000)
        self.assertEqual(c[0]['buyers'][0]['role'], '이사 · CEO')
        self.assertFalse(review)

    def test_derivatives_never_counted(self):
        xml = XML.format(rows='', note='').replace('</ownershipDocument>', '<derivativeTable>'+ROW.format(title='Common Stock Option', code='P', side='A', price='1')+'</derivativeTable></ownershipDocument>')
        self.assertFalse(parse_document(xml, {'filed': '2026-09-21'}, 'https://www.sec.gov/a')['transactions'])

    def test_date_and_filing_cutoff_no_lookahead(self):
        old = document();old['transactions'][0]['date'] = '2026-03-21'
        late = document(accession='late');late['filed'] = '2026-09-22'
        future = document(accession='future');future['transactions'][0]['date'] = '2026-09-22'
        self.assertEqual(report([old, late, future]), ([], []))
        edge = document();edge['transactions'][0]['date'] = '2026-08-22'
        self.assertTrue(report([edge])[0][0]['active'])
        edge['transactions'][0]['date'] = '2026-08-21'
        self.assertFalse(report([edge])[0][0]['active'])

    def test_split_fills_and_joint_duplicate(self):
        d = document();t = copy.deepcopy(d['transactions'][0]);t['price'] = 11;t['value'] = 1100
        d['transactions'].append(t)
        duplicate = copy.deepcopy(d);duplicate['accession'] = 'b'
        c, _ = report([d, duplicate])
        self.assertEqual(c[0]['value'], 2100)
        self.assertEqual(c[0]['day_count'], 1)
        self.assertEqual(c[0]['buyer_count'], 1)

    def test_repeat_and_current_buyer(self):
        d = document();old = document(accession='old');old['transactions'][0]['date'] = '2026-07-01'
        c, _ = report([old, d])
        self.assertEqual(c[0]['pattern'], '여러 달 반복')
        self.assertTrue(c[0]['same_buyer_repeat'])
        self.assertEqual(c[0]['value'], 1000)
        self.assertEqual(c[0]['six_month_value'], 2000)
        old['owners'][0]['cik'] = '3'
        self.assertFalse(report([old, d])[0][0]['same_buyer_repeat'])

    def test_correction_without_p_quarantines_original(self):
        original = document();amend = document(code='S', accession='amend');amend['form'] = '4/A'
        c, review = report([original, amend])
        self.assertFalse(c)
        self.assertEqual(len(review), 1)
        amend['filed'] = '2026-09-22'
        self.assertEqual(len(report([original, amend])[0]), 1)

    def test_amendment_with_corrected_period_uses_original_filing_date(self):
        original = document();amend = document(code='S', accession='amend');amend['form'] = '4/A'
        amend['period'] = '2026-09-18';amend['original_filed'] = '2026-09-21'
        c, review = report([original, amend])
        self.assertFalse(c)
        self.assertEqual(len(review), 1)

    def test_inconsistent_notes_and_missing_price(self):
        for doc in [document(note='Shares sold at weighted average price'), document(price=''), document(title='Security')]:
            c, review = report([doc])
            self.assertFalse(c)
            self.assertEqual(len(review), 1)

    def test_bulk_filing_filter_and_amendments(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as z:
            for name, rows in [('SUBMISSION', [
                {'ACCESSION_NUMBER': 'a', 'FILING_DATE': '21-SEP-2026', 'ISSUERTRADINGSYMBOL': 'TEST', 'DOCUMENT_TYPE': '4', 'ISSUERCIK': '1'},
                {'ACCESSION_NUMBER': 'b', 'FILING_DATE': '22-SEP-2026', 'ISSUERTRADINGSYMBOL': 'TEST', 'DOCUMENT_TYPE': '4', 'ISSUERCIK': '1'},
                {'ACCESSION_NUMBER': 'c', 'FILING_DATE': '21-SEP-2026', 'ISSUERTRADINGSYMBOL': 'TEST', 'DOCUMENT_TYPE': '4/A', 'ISSUERCIK': '1'}]),
                ('NONDERIV_TRANS', [{'ACCESSION_NUMBER': a, 'TRANS_CODE': 'P', 'TRANS_ACQUIRED_DISP_CD': 'A'} for a in ['a', 'b']])]:
                buf = io.StringIO();w = csv.DictWriter(buf, fieldnames=list(rows[0]), delimiter='\t');w.writeheader();w.writerows(rows)
                z.writestr(name+'.tsv', buf.getvalue())
        filings, known = quarter_candidates(stream.getvalue(), {'TEST'}, date(2026, 3, 22), date(2026, 9, 21))
        self.assertEqual({f['accession'] for f in filings}, {'a', 'c'})
        self.assertEqual(known, {'a', 'c'})


if __name__ == '__main__':
    unittest.main()
