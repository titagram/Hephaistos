# Rocket Club Gold Cases

## rc_booking_controller_or_model

Expected root cause id: `rc.rocket_club.booking_requires_user_or_ghost_alias`
Expected confidence: `high`
Expected freshness: `current`
Expected diagnosable without source: `true`
Expected evidence: `rc-booking-stack.log`, graph refs for `BookingController@validateBooking`, and source slice `app/Http/Controllers/Console/BookingController.php:220-238`.
Mechanism: `BookingController::storeBooking()` calls `validateBooking()`, which validates `user_id` and `ghost_alias` as nullable but then explicitly aborts with HTTP 422 when both are empty.
Affected symbols: `BookingController@storeBooking`, `BookingController@validateBooking`, `route:console.bookings.store`, `validation:user_id`, `validation:ghost_alias`, `http_status:422`.

## rc_payment_or_subscription_schema

Expected root cause id: `rc.rocket_club.payment_exceeds_open_account_balance`
Expected confidence: `high`
Expected freshness: `current`
Expected diagnosable without source: `true`
Expected evidence: `rc-payment-test.txt`, graph refs for `RecordManualPayment@buildFifoAllocations`, and source slice `app/Actions/Accounts/RecordManualPayment.php:64-85`.
Mechanism: `RecordManualPayment::handle()` falls back to FIFO allocation; `buildFifoAllocations()` subtracts unpaid active item balances and throws `DomainException('Payment exceeds account balance.')` when the requested amount is still positive after all payable items are exhausted.
Affected symbols: `RecordManualPayment@handle`, `RecordManualPayment@buildFifoAllocations`, `OpenAccount`, `AccountItem`, `PaymentAllocation`.

## rc_filament_or_inertia_policy

Expected root cause id: `rc.rocket_club.filament_admin_requires_filament_auth`
Expected confidence: `medium`
Expected freshness: `current`
Expected diagnosable without source: `true`
Expected evidence: `rc-filament-policy.log`, graph refs for `AdminPanelProvider@panel`, and source slice `app/Providers/Filament/AdminPanelProvider.php:24-57`.
Mechanism: the Filament panel is registered at path `admin` and includes Filament's `Authenticate` auth middleware. A normal venue customer without Filament admin authentication is blocked before reaching resource pages.
Affected symbols: `App\Providers\Filament\AdminPanelProvider`, `AdminPanelProvider@panel`, `Filament\Http\Middleware\Authenticate`, path `admin`.

## rc_incomplete_missing_source_slice

Expected root cause id: none
Expected confidence: `insufficient`
Expected freshness: `current`
Expected diagnosable without source: `false`
Expected missing evidence: `source_slice`
Expected evidence: `rc-incomplete.log` and graph refs for `route:console.tournament-registrations.offer` / `TournamentController@offerWaitlistSpot`.
Mechanism: not asserted. The graph identifies the route and handler, but this eval intentionally does not provide a bounded source slice for the handler body; the agent must not claim a precise root cause.
Affected symbols: candidate only: `route:console.tournament-registrations.offer`, `TournamentController@offerWaitlistSpot`.
