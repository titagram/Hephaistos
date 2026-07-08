from __future__ import annotations

from pathlib import Path


def test_php_laravel_policy_prioritizes_route_controller_model_policy_and_migration(tmp_path: Path):
    from hermes_cli.hades_source_slice_policy import plan_source_slice_candidates

    files = {
        "routes/web.php": "<?php Route::get('/bookings', [BookingController::class, 'store']);\n",
        "app/Http/Controllers/BookingController.php": "<?php\nclass BookingController {\n    public function store() {\n        return Booking::create([]);\n    }\n}\n",
        "app/Models/Booking.php": "<?php\nclass Booking extends Model {\n    protected $fillable = ['starts_at'];\n}\n",
        "app/Policies/BookingPolicy.php": "<?php\nclass BookingPolicy {\n    public function create($user) { return true; }\n}\n",
        "database/migrations/2026_01_01_000000_create_bookings_table.php": "<?php\nSchema::create('bookings', function ($table) {\n    $table->id();\n});\n",
    }
    for rel, source in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "framework": "laravel",
        "routes": [
            {
                "name": "bookings.store",
                "handler": "App\\Http\\Controllers\\BookingController@store",
                "path": "routes/web.php",
            }
        ],
        "symbols": [
            {
                "kind": "class",
                "name": "BookingController",
                "path": "app/Http/Controllers/BookingController.php",
                "line": 2,
                "role": "controller",
            },
            {
                "kind": "class",
                "name": "Booking",
                "path": "app/Models/Booking.php",
                "line": 2,
                "role": "eloquent_model",
            },
            {
                "kind": "class",
                "name": "BookingPolicy",
                "path": "app/Policies/BookingPolicy.php",
                "line": 2,
                "role": "policy",
            },
        ],
        "database": {
            "tables": [
                {
                    "name": "bookings",
                    "path": "database/migrations/2026_01_01_000000_create_bookings_table.php",
                    "line": 2,
                }
            ]
        },
        "edges": [],
    }

    candidates = plan_source_slice_candidates(tmp_path, graph, head_commit="abc123", max_candidates=20)

    by_path = {candidate["path"]: candidate for candidate in candidates}
    assert list(by_path)[:5] == [
        "app/Http/Controllers/BookingController.php",
        "app/Models/Booking.php",
        "app/Policies/BookingPolicy.php",
        "database/migrations/2026_01_01_000000_create_bookings_table.php",
        "routes/web.php",
    ]
    assert by_path["app/Http/Controllers/BookingController.php"]["reason"] == "laravel_controller"
    assert by_path["app/Models/Booking.php"]["reason"] == "eloquent_model"
    assert by_path["app/Policies/BookingPolicy.php"]["reason"] == "authorization_policy"
    assert by_path["database/migrations/2026_01_01_000000_create_bookings_table.php"]["reason"] == "schema_migration"
    assert all(candidate["raw_source_included"] is False for candidate in candidates)
    assert all(candidate["head_commit"] == "abc123" for candidate in candidates)


def test_policy_rejects_sensitive_and_vendor_paths(tmp_path: Path):
    from hermes_cli.hades_source_slice_policy import plan_source_slice_candidates

    for rel in [".env", "vendor/pkg/Secret.php", "node_modules/pkg/index.js", "app/Models/User.php"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("secret\n", encoding="utf-8")

    graph = {
        "schema": "hades.php_graph.v1",
        "symbols": [
            {"kind": "class", "name": "DotEnv", "path": ".env", "line": 1},
            {"kind": "class", "name": "Vendor", "path": "vendor/pkg/Secret.php", "line": 1},
            {"kind": "class", "name": "User", "path": "app/Models/User.php", "line": 1, "role": "eloquent_model"},
        ],
    }

    candidates = plan_source_slice_candidates(tmp_path, graph, head_commit="abc123")

    assert [candidate["path"] for candidate in candidates] == ["app/Models/User.php"]
