<?php

use Illuminate\Database\Migrations\Migration;
use Illuminate\Database\Schema\Blueprint;
use Illuminate\Support\Facades\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('hades_source_slice_candidates', function (Blueprint $table): void {
            $table->id();
            $table->foreignUlid('project_id')->constrained()->cascadeOnDelete();
            $table->foreignUlid('workspace_binding_id')->constrained('hades_workspace_bindings')->cascadeOnDelete();
            $table->string('candidate_key', 64);
            $table->string('path', 1024);
            $table->unsignedInteger('start_line');
            $table->unsignedInteger('end_line');
            $table->string('symbol', 512)->nullable();
            $table->string('reason', 128);
            $table->unsignedInteger('priority')->default(500);
            $table->string('head_commit', 80)->nullable();
            $table->string('status', 64)->default('pending');
            $table->foreignUlid('job_id')->nullable()->constrained('hades_agent_jobs')->nullOnDelete();
            $table->foreignUlid('source_slice_id')->nullable()->constrained('hades_source_slices')->nullOnDelete();
            $table->json('metadata')->nullable();
            $table->timestamps();

            $table->unique(['workspace_binding_id', 'candidate_key'], 'hades_slice_candidate_binding_key_unique');
            $table->index(['project_id', 'workspace_binding_id', 'status'], 'hades_slice_candidate_status_index');
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('hades_source_slice_candidates');
    }
};
