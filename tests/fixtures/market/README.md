# Tencent A-share turnover samples

Captured 2026-11-14 from the Tencent Finance day endpoint:
[Shanghai A shares](https://web.ifzq.gtimg.cn/appstock/app/day/query?code=sh000002)
and [Shenzhen A shares](https://web.ifzq.gtimg.cn/appstock/app/day/query?code=sz399107).

Each file keeps only the instrument identity and the original minute rows
and previous closes for 2026-11-12 and 2026-11-13. Each minute row contains
Beijing time (HHMM), price, cumulative volume, and cumulative yuan turnover.
The expected values in the tests were calculated independently by aligning
the two yuan amounts on each date and minute before converting to 亿元.
