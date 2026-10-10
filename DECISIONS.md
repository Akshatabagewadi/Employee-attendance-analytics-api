# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
I use a unique index on employees.emp_code to prevent duplicate employee codes. I use a compound index on attendance_logs.emp_code and date to support employee attendance lookups and prevent duplicate attendance records for the same employee and date. Date-based indexes can also help attendance reports. I would avoid adding indexes that do not improve important queries because indexes increase storage and write costs.


2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
Two identical punch-in requests may arrive at the same time. Application-level duplicate checks alone are not sufficient. A unique compound index on emp_code and date ensures that only one attendance record can be created. The losing request should return a suitable conflict response.


3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
The leaderboard should use late minutes as its ranking metric. When employees tie at the cutoff, the implementation should follow a clearly defined ranking policy and return consistent results. The exact behavior depends on whether the query uses rank or dense rank and how the limit is applied.


4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
Department headcount should come from employee records, not attendance records. This ensures employees with zero attendance logs are still counted. Attendance metrics should be calculated separately and combined with employee information


5. **One thing you would change** if this had to serve 100x the data.
I would review query execution plans, add indexes based on actual query patterns, and measure aggregation performance. I would also consider pagination, monitoring, and load testing before introducing caching or other optimizations.
