#!/usr/bin/env python3
"""
Raster_Dev_xyz — Companion app for the WomblePulse clawbot contract.
Provides CLI and programmatic access to trade, invest, deposit, and query
WomblePulse vault, orders, strategies, positions, and rounds.
All outputs in one single file; no split modules.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import hashlib
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

# Optional deps for EVM; fallback if not installed
try:
    from web3 import Web3
    from web3.contract import Contract
    from web3.types import TxReceipt, Wei
    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False
    Web3 = None
    Contract = None
    TxReceipt = None
    Wei = None

# -----------------------------------------------------------------------------
# Constants (Raster_Dev_xyz-specific; do not reuse in other apps)
# -----------------------------------------------------------------------------

RASTER_DEV_XYZ_APP_NAME = "Raster_Dev_xyz"
RASTER_DEV_XYZ_VERSION = "1.0.0"
RASTER_DEV_XYZ_CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".raster_dev_xyz")
RASTER_DEV_XYZ_CONFIG_FILE = os.path.join(RASTER_DEV_XYZ_CONFIG_DIR, "config.json")
RASTER_DEV_XYZ_DEFAULT_RPC = "https://eth.llamarpc.com"
RASTER_DEV_XYZ_CHAIN_ID_MAINNET = 1
RASTER_DEV_XYZ_CHAIN_ID_SEPOLIA = 11155111
RASTER_DEV_XYZ_CHAIN_ID_BASE = 8453
RASTER_DEV_XYZ_GAS_LIMIT_DEFAULT = 300_000
RASTER_DEV_XYZ_GAS_MULTIPLIER = 1.2
RASTER_DEV_XYZ_MAX_RETRIES = 5
RASTER_DEV_XYZ_RETRY_DELAY_SEC = 2.0
RASTER_DEV_XYZ_HEX_PREFIX = "0x"
RASTER_DEV_XYZ_ADDRESS_BYTES = 20
RASTER_DEV_XYZ_ADDRESS_HEX_LEN = 40
RASTER_DEV_XYZ_BPS_BASE = 10_000
RASTER_DEV_XYZ_DEFAULT_SLIPPAGE_BPS = 50
RASTER_DEV_XYZ_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
RASTER_DEV_XYZ_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
RASTER_DEV_XYZ_EMPTY_BYTES32 = "0x" + "00" * 32
RASTER_DEV_XYZ_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
RASTER_DEV_XYZ_NAMESPACE_SALT = "raster_dev_xyz_womblepulse_v1"
RASTER_DEV_XYZ_DOMAIN_TAG_HEX = "0x6b8d2f1a4c7e9b0d3f6a8c1e4b7d0a3c6e9f2b5d8a1c4e7b0d3f6a9c2e5b8d1f4a"

# -----------------------------------------------------------------------------
# EIP-55 checksum (for address literals; unique namespace)
# -----------------------------------------------------------------------------


def _keccak256_hex(data: bytes) -> str:
    """Keccak-256 hex digest; use hashlib if no eth lib."""
    if HAS_WEB3:
        return Web3.keccak(data).hex()
    h = hashlib.sha3_256(data) if hasattr(hashlib, "sha3_256") else hashlib.sha256(data)
    return h.hexdigest()


def to_checksum_address(address_hex: str) -> str:
    """Convert 0x-prefixed 40-char hex address to EIP-55 checksummed form."""
    addr = address_hex.lower().strip()
    if addr.startswith("0x"):
        addr = addr[2:]
    if len(addr) != RASTER_DEV_XYZ_ADDRESS_HEX_LEN:
        raise ValueError(f"Address must be {RASTER_DEV_XYZ_ADDRESS_HEX_LEN} hex chars after 0x")
    try:
        if HAS_WEB3:
            return Web3.to_checksum_address("0x" + addr)
    except Exception:
        pass
    raw = addr.encode("ascii")
    digest = _keccak256_hex(raw)
    result = []
    for i, c in enumerate(addr):
        if c in "0123456789":
            result.append(c)
        else:
            nibble = int(digest[i], 16)
            result.append(c.upper() if nibble >= 8 else c.lower())
    return "0x" + "".join(result)


def random_address_eip55() -> str:
    """Generate a random 20-byte address and return EIP-55 checksummed."""
    raw = os.urandom(RASTER_DEV_XYZ_ADDRESS_BYTES)
    addr_hex = raw.hex()
    return to_checksum_address("0x" + addr_hex)


def generate_unique_addresses(count: int = 8) -> List[str]:
    """Generate `count` unique EIP-55 addresses (e.g. for contract deployment)."""
    seen: set = set()
    out: List[str] = []
    while len(out) < count:
        a = random_address_eip55()
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

import logging

_logger: Optional[logging.Logger] = None


def get_logger(name: str = "raster_dev_xyz") -> logging.Logger:
    global _logger
    if _logger is None:
        _logger = logging.getLogger(name)
        if not _logger.handlers:
            h = logging.StreamHandler(sys.stderr)
            h.setFormatter(logging.Formatter(RASTER_DEV_XYZ_LOG_FORMAT, RASTER_DEV_XYZ_DATE_FORMAT))
            _logger.addHandler(h)
            _logger.setLevel(logging.INFO)
    return _logger


def set_verbose(verbose: bool) -> None:
    get_logger().setLevel(logging.DEBUG if verbose else logging.INFO)


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------


@dataclass
class Raster_Dev_xyzConfig:
    rpc_url: str = RASTER_DEV_XYZ_DEFAULT_RPC
    chain_id: int = RASTER_DEV_XYZ_CHAIN_ID_MAINNET
    contract_address: Optional[str] = None
    private_key: Optional[str] = None
    gas_limit: int = RASTER_DEV_XYZ_GAS_LIMIT_DEFAULT
    gas_multiplier: float = RASTER_DEV_XYZ_GAS_MULTIPLIER
    max_fee_per_gas_gwei: Optional[float] = None
    max_priority_fee_gwei: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rpc_url": self.rpc_url,
            "chain_id": self.chain_id,
            "contract_address": self.contract_address,
            "gas_limit": self.gas_limit,
            "gas_multiplier": self.gas_multiplier,
            "max_fee_per_gas_gwei": self.max_fee_per_gas_gwei,
            "max_priority_fee_gwei": self.max_priority_fee_gwei,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Raster_Dev_xyzConfig:
        return cls(
            rpc_url=d.get("rpc_url", RASTER_DEV_XYZ_DEFAULT_RPC),
            chain_id=int(d.get("chain_id", RASTER_DEV_XYZ_CHAIN_ID_MAINNET)),
            contract_address=d.get("contract_address"),
