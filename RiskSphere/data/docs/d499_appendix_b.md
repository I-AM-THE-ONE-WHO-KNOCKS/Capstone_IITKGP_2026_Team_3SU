# BCBS-IOSCO d499, Appendix B: standardised haircut schedule

Source: Basel Committee on Banking Supervision and IOSCO, "Margin requirements for non-centrally cleared derivatives", April 2020 (d499), Appendix B, printed page 26.
URL: https://www.bis.org/publications/202004-standards-margin-requirements-non-centrally-cleared-derivatives.pdf
Retrieved: 2026-11-20. SHA-256 of the PDF: 3ba9da23ae55c5dc4ad482e06eb084ae9c9cae7400a83f5778e986dae490ece6
Licence: "Brief excerpts may be reproduced or translated provided the source is stated." (c) Bank for International Settlements 2020. Only this table is reproduced. The full PDF is downloaded by `scripts/fetch_documents.py` and is not committed.
Text as extracted by pypdf. Haircut in percent of market value.

| Asset class | Haircut (% of market value) |
|---|---|
| Cash in same currency | 0 |
| High-quality government and central bank securities: residual maturity less than one year | 0.5 |
| High-quality government and central bank securities: residual maturity between one and five years | 2 |
| High-quality government and central bank securities: residual maturity greater than five years | 4 |
| High-quality corporate\covered bonds: residual maturity less than one year | 1 |
| High-quality corporate\covered bonds: residual maturity greater than one year and less than five years | 4 |
| High-quality corporate\covered bonds: residual maturity greater than five years | 8 |
| Equities included in major stock indices | 15 |
| Gold | 15 |
| Additional (additive) haircut on asset in which the currency of the derivatives obligation differs from that of the collateral asset | 8 |

Footnote 19 of the same document: "The haircuts in Appendix B are based on the standard supervisory haircuts that appear in paragraph 151, part 2, Basel II".

## How the calculator's table compares

| Calculator cell | In d499? |
|---|---|
| cash, government AA- or better, corporate AA- or better, main index equity, gold | Yes, same values |
| government and corporate A+ to BBB- | No. Extension, likely from Basel II paragraph 151 (not confirmed) |
| other equity, 25% | No. Extension |
| 8% currency mismatch add-on | In d499, not modelled. Every collateral row and trade in the sample book is in USD, so it does not change today's figures |
