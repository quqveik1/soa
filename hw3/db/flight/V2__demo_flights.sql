INSERT INTO airlines(code, name) VALUES ('SU', 'Aeroflot');

INSERT INTO flights(id, airline_code, flight_number, origin, destination,
                    departure_at, arrival_at, total_seats, available_seats, price, status)
VALUES
('11111111-1111-4111-8111-111111111111', 'SU', 'SU1000', 'SVO', 'LED',
 (CURRENT_DATE + 1 + TIME '10:00') AT TIME ZONE 'UTC',
 (CURRENT_DATE + 1 + TIME '11:30') AT TIME ZONE 'UTC', 50, 50, 7500.00, 'SCHEDULED'),
('22222222-2222-4222-8222-222222222222', 'SU', 'SU1002', 'SVO', 'LED',
 (CURRENT_DATE + 2 + TIME '15:00') AT TIME ZONE 'UTC',
 (CURRENT_DATE + 2 + TIME '16:30') AT TIME ZONE 'UTC', 1, 1, 8200.00, 'SCHEDULED'),
('33333333-3333-4333-8333-333333333333', 'SU', 'SU1001', 'LED', 'SVO',
 (CURRENT_DATE + 1 + TIME '18:00') AT TIME ZONE 'UTC',
 (CURRENT_DATE + 1 + TIME '19:30') AT TIME ZONE 'UTC', 30, 30, 7100.00, 'CANCELLED');
