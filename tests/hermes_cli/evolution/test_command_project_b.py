"""Tests for Project B CLI subcommand parsing and execution."""

from argparse import Namespace
import json
import pytest
from pathlib import Path

from hermes_cli.evolution.command import evolution_command
from hermes_cli.evolution.telos_contract import (
    CapabilityDirection,
    DesiredTrait,
    Priority,
    ProactivityPolicy,
    Prohibition,
    SuccessIndicator,
    TelosRevision,
)
from hermes_cli.evolution.telos_store import TelosStore


def test_evolution_command_init_and_status(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    args = Namespace(action="init", json=True)
    res = evolution_command(args)
    assert res == 0

    args_status = Namespace(action="status", json=True)
    res_status = evolution_command(args_status)
    assert res_status == 0


def test_evolution_command_doctor(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    org_root = tmp_path / "organism"
    args = Namespace(action="doctor", json=True, org_root=org_root)
    res = evolution_command(args)
    assert res == 0



def test_evolution_command_telos_actions(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    org_root = tmp_path / "organism"
    tstore = TelosStore(org_root)

    telos = TelosRevision(
        schema_version=1,
        organism_id="00000000-0000-0000-0000-000000000000",
        parent_digest=None,
        purpose="Testing Telos",
        desired_traits=(DesiredTrait("trait1", "Trait 1", ("t1",), 5),),
        capability_directions=(CapabilityDirection("cap1", "Cap 1", ("c1",), 4),),
        priorities=(Priority("p1", "P1", ("p1",), 5),),
        tradeoffs=(),
        prohibitions=(Prohibition("pro1", "Pro 1", ("pr1",), 5),),
        proactivity_policy=ProactivityPolicy("pass", "Pass", ("pass",), 3),
        success_indicators=(SuccessIndicator("ind1", "Ind 1", ("i1",), 4),),
    )
    tstore.save_revision(telos)
    digest = telos.canonical_digest

    receipt = tstore.issue_approval_receipt(digest, action="activate")
    args_approve = Namespace(action="telos_approve", digest=digest, receipt=receipt.receipt_id, json=True, org_root=org_root)
    res_app = evolution_command(args_approve)
    assert res_app == 0


    args_status = Namespace(action="telos_status", json=True)
    res_stat = evolution_command(args_status)
    assert res_stat == 0
