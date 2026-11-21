# Dashboard Requirements Note (Meeting Room Booking)
Node: requirements_clarification. Source: sandboxes/258c8916685642f3a261281a8ffde149 (bookings.db; tables rooms, bookings).

DATA-MODEL FACTS
- bookings(id, room_id, date 'YYYY-MM-DD', start_min, end_min [minutes from midnight], title, organiser, status, created_at, cancelled_at). status is ONLY 'confirmed' or 'cancelled'.
- Overlap within a room among confirmed bookings is impossible (enforced at insert). REJECTED bookings are NOT stored. There is NO no-show/attendance/check-in field.
- Rooms: Boardroom(12), Meeting Room A(8), Huddle Room B(4). Working hours Mon-Fri 09:00-18:00, 30-min slots, Asia/Hong_Kong (UTC+8).

METRIC SPEC (decisions; implement exactly)
1. Date range: params start_date,end_date; INCLUSIVE both ends (date>=start AND date<=end). Default = full data span.
2. Total time reserved = SUM(end_min-start_min) over status='confirmed' in range; minutes and hours; per room + system total. No within-room overlaps to de-dup; system total sums across rooms. Attendance not available => reserved = scheduled only.
3. Cancellation = status='cancelled'. cancel_rate = cancelled / all bookings in range. Also report cancelled count + minutes. No-show/rejected/expired NOT representable => excluded from v1 (need new capture).
4. Busiest = BOTH: (a) per weekday Mon-Fri: confirmed count + occupied hours; (b) per hour 09:00-17:00: each 30-min slot value = number of confirmed bookings covering that slot.
5. Booking frequency = confirmed count per room (primary) + reserved hours + utilization% = occupied_min/(workdays_in_range*540).
6. Charts: bar (frequency/room), bar (reserved hours+util/room), heatmap weekday x hour, bar (bookings/day), bar (confirmed vs cancelled). Delivery: standalone read-only static HTML+JS page reading bookings.db, separate from CLI. OPEN.
7. Audience: single manager, read-only, no auth. CSV export required; PDF optional. OPEN.

OPEN QUESTIONS (defaults in parentheses)
Q1 Default range? (full span)
Q2 Scheduled minutes only? (yes)
Q3 Ship with confirmed+cancelled only? (yes)
Q4 Bucket both weekday and hour? (yes)
Q5 Frequency = count + hours? (yes)
Q6 Standalone static HTML vs in-app/Flask? (standalone HTML)
Q7 One manager + CSV? (yes)