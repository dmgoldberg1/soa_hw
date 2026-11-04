INSERT INTO airlines (code, name) VALUES ('SU', 'Аэрофлот');
INSERT INTO flight_numbers VALUES ('SU100', 1), ('SU101', 1);
INSERT INTO flights VALUES
    ('11111111-1111-1111-1111-111111111111', 'SU100', 'SVO', 'LED',
     date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' + interval '7 days 10 hours',
     date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' + interval '7 days 12 hours',
     10, 10, 1500000, 'SCHEDULED'),
    ('22222222-2222-2222-2222-222222222222', 'SU101', 'LED', 'SVO',
     date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' + interval '7 days 14 hours',
     date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' + interval '7 days 16 hours',
     20, 20, 1200000, 'SCHEDULED');
