# CNCBridge test script

Do each step and compare with what you should see. Start with the Demo control window open,
CNCBridge open on the demo workspace, and the customer inbox open in a browser.

## Part 1: start

1. Do: press Start monitoring in CNCBridge.
   See: the state changes to Monitoring. All counters are 0.

## Part 2: the fixed test files

Copy the files one at a time with Copy one, or all together with Copy all test files.
They are processed in the order of their numbers.

2. Do: copy 01_normal_board.csv into the machine folder. A standard board with 12 parts.
   See: Processed. The output is in the shared folder and in the customer inbox. Board serial is B240001.

3. Do: copy 02_long_board.csv into the machine folder. A board with 20 parts, so the parts table is longer.
   See: Processed. Placements has 20 rows. Summary still has 7 rows.

4. Do: copy 03_board_with_errors.csv into the machine folder. A board where two placements failed.
   See: Processed. Summary shows Errors 2 and Result FAIL.

5. Do: copy 04_single_placement.csv into the machine folder. The shortest possible board: one part.
   See: Processed. Placements has 1 row.

6. Do: copy 05_semicolon_decimal_comma.csv into the machine folder. European export: semicolons between columns, comma as decimal sign.
   See: Processed like file 01. Numbers keep their comma, for example Yield_pct 100,0.

7. Do: copy 06_windows_1252.csv into the machine folder. Special characters saved in the Windows code page instead of UTF-8.
   See: Processed. Program reads ECU-CAFÉ-REV3 and the first part is TEMP-SENS-85°C.

8. Do: copy 07_copy_of_01.csv into the machine folder. Exactly the same content as file 01 under another name.
   See: Not sent. History shows duplicate. The file is in back-up\_duplicates.

9. Do: copy 08_not_a_board_log.csv into the machine folder. An alarm log with nothing in any of the chosen cells.
   See: Not sent. History shows failed. The file is in back-up\_failed.

After all 8 files: Done 6, Could not be read 1, Waiting to be read 0,
and the customer inbox holds 6 files.

## Part 3: live cases

10. Do: Press Start machine in the Demo control window.
   See: A new board file appears every few seconds. Done counts up. Each one shows in the customer inbox.

11. Do: In CNCBridge open the tab Pick ranges & preview and press Newest machine file.
   See: The file is shown as a grid. The four chosen ranges are tinted and numbered 1 to 4.

12. Do: On the Setup tab change Operator to OP-99 and press Save settings. Do not stop monitoring.
   See: The next output file has Operator OP-99.

13. Do: On the Setup tab choose CSV, press Save settings. Then choose TXT and save again.
   See: New files in the shared folder and the customer inbox end in .csv, then .txt.

14. Do: Press Simulate outage in the Demo control window and wait for two or three boards.
   See: Waiting to be sent goes up. The shared folder still receives every file. Nothing is lost.

15. Do: Press Simulate outage again to end it.
   See: Waiting to be sent returns to 0 within a few seconds. The customer inbox receives the waiting files in order.

16. Do: Press Refuse next file.
   See: Refused shows 1. That file is in data\rejected. The boards after it are delivered.

17. Do: Press Send a duplicate.
   See: History shows duplicate. Nothing new reaches the customer inbox.

18. Do: Press Drop a broken file.
   See: Could not be read shows 1 more. The file is in back-up\_failed. Monitoring continues.

19. Do: Press Write a file slowly.
   See: Waiting to be read shows the file for about 12 seconds. It is processed only after it is complete.

20. Do: Press Stop in CNCBridge, let two boards arrive, then press Start monitoring.
   See: The two waiting boards are processed straight away.

21. Do: Use Open folder > Back-up folder in CNCBridge.
   See: Today's folder holds every processed machine file. The machine folder is empty.
