# CNCBridge presentation script

Start the integrated demo, leave JSON selected, and start monitoring.
Use Copy one in the order below. Check History, Shared folder and the MES inbox.

## 01_normal_board.csv

12 placements, serial B240001, both manual inputs, source in dated back-up.

## 02_long_board.csv

20 placements; the variable range stops before the feeder table.

## 03_board_with_errors.csv

Summary reports 2 errors and FAIL; this valid machine log is still delivered.

## 04_single_placement.csv

Exactly one placement; the seven summary entries remain intact.

## 05_semicolon_decimal_comma.csv

Semicolon delimiter detected; Yield_pct is 100,0; nine placement columns.

## 06_windows_1252.csv

Windows-1252 characters survive in the UTF-8 output.

## 07_copy_of_01.csv

Duplicate blocked; source in backup/_duplicates; no second delivery.

## 08_not_a_board_log.csv

No selected range contains data; source in backup/_failed; next file continues.

## Outage and recovery

Click Simulate outage, then Drop one board. Shared delivery should succeed while API is queued.
Click End outage. The API queue should drain and the MES inbox should gain the file.

## Slow file

Click Write a file slowly. A temporary .part file is written first; monitoring picks up the CSV
after it is complete and renamed. The source then appears in the dated back-up folder.

## Automated proof

Run python -m demo.verify to check all three formats, both destinations, archive, duplicate
blocking, bad-file handling, and recovery after a real HTTP 503 response.
