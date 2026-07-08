<?php

require __DIR__.'/vendor/autoload.php';

$app = require __DIR__.'/bootstrap/app.php';
$app->make(Illuminate\Contracts\Console\Kernel::class)->bootstrap();

$projectId = (string) Illuminate\Support\Str::ulid();
$name = 'Rocket Club Awareness Hardening Eval';
$slug = 'rocket-club-awareness-hardening-eval-'.strtolower(substr($projectId, -6));
$now = now();

Illuminate\Support\Facades\DB::table('projects')->insert([
    'id' => $projectId,
    'name' => $name,
    'slug' => $slug,
    'description' => 'Fresh Hades no-codebase awareness hardening regression project.',
    'status' => 'active',
    'default_code_exposure_policy' => 'bounded_source_slices',
    'created_by_user_id' => 50,
    'created_at' => $now,
    'updated_at' => $now,
]);

$token = app(App\Services\Hades\HadesTokenService::class)->createBootstrapToken(
    $projectId,
    'Rocket Club Awareness Hardening Eval bootstrap',
    7,
    ['read_source_slice', 'sync_git_tree', 'populate_backend_ast'],
);

echo json_encode([
    'project_id' => $projectId,
    'project_name' => $name,
    'project_slug' => $slug,
    'project_token' => $token['plain_token'],
], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES), PHP_EOL;
