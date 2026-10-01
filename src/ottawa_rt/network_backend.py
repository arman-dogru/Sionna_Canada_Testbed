"""NVIDIA Sionna SYS adapter; no analytical throughput fallback."""

from __future__ import annotations

import numpy as np

from ottawa_rt.network_models import NetworkScenario


class SionnaBackend:
    def __init__(self, scenario: NetworkScenario, serving: np.ndarray, sinr_db: np.ndarray):
        try:
            import torch
            from sionna.phy import config
            from sionna.phy.nr.utils import decode_mcs_index
            from sionna.sys import InnerLoopLinkAdaptation, PFSchedulerSUMIMO, PHYAbstraction
        except ImportError as exc:
            raise RuntimeError(
                'Install the network extra: python -m pip install -e ".[network]"'
            ) from exc
        self.torch = torch
        self.device = (
            "cuda" if scenario.device == "auto" and torch.cuda.is_available() else scenario.device
        )
        if self.device == "auto":
            self.device = "cpu"
        if self.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA PyTorch is unavailable; use device: cpu or install a CUDA wheel"
            )
        config.seed = scenario.seed
        self.scenario = scenario
        self.num_users = len(serving)
        self.num_cells = max(int(serving.max()) + 1, 1)
        self.mask = torch.as_tensor(
            np.arange(self.num_cells)[:, None] == serving[None, :], device=self.device
        )
        self.sinr = torch.as_tensor(
            np.power(10.0, np.clip(sinr_db, -100, 60) / 10),
            dtype=torch.float32,
            device=self.device,
        ).expand(self.num_cells, -1)
        self.scheduler = PFSchedulerSUMIMO(
            self.num_users,
            scenario.num_rb,
            1,
            batch_size=[self.num_cells],
            beta=scenario.pf_beta,
            device=self.device,
        )
        self.phy = PHYAbstraction(device=self.device)
        illa = InnerLoopLinkAdaptation(
            self.phy,
            bler_target=scenario.bler_target,
            device=self.device,
        )
        # Static maps allow precomputation for every possible RB allocation.
        counts = torch.arange(scenario.num_rb + 1, device=self.device, dtype=torch.int32)
        resources = counts[:, None].expand(-1, self.num_users) * 12 * scenario.data_symbols
        snr = self.sinr[0].expand(scenario.num_rb + 1, -1)
        with torch.no_grad():
            self.mcs_table = illa(
                sinr_eff=snr,
                num_allocated_re=resources,
                mcs_table_index=1,
                mcs_category=1,
            )
        self.previous = torch.zeros(
            (self.num_cells, self.num_users),
            dtype=torch.float32,
            device=self.device,
        )
        full_mcs = self.mcs_table[-1].expand(self.num_cells, -1)
        with torch.no_grad():
            _, _, _, predicted_tbler, _ = self.phy(
                full_mcs,
                sinr_eff=self.sinr,
                num_allocated_re=torch.full_like(
                    full_mcs, scenario.num_rb * 12 * scenario.data_symbols
                ),
                mcs_table_index=1,
                mcs_category=1,
            )
            order, coderate = decode_mcs_index(full_mcs, table_index=1, is_pusch=False)
            self.rate = order * coderate * (1 - predicted_tbler) * 12 * scenario.data_symbols
        self.users = torch.arange(self.num_users, device=self.device)

    def step(self, active: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        torch = self.torch
        with torch.no_grad():
            eligible = (
                self.mask & torch.as_tensor(active, device=self.device)[None, :] & (self.rate > 0)
            )
            rates = (self.rate * eligible)[:, None, None, :].expand(
                -1,
                1,
                self.scenario.num_rb,
                -1,
            )
            # Zero history otherwise underflows during long idle periods and
            # causes 0/0 PF metrics for UEs associated with another cell.
            scheduled = self.scheduler(self.previous.clamp(min=1e-6), rates)
            rb = scheduled.sum(dim=(1, 2, 4)).to(torch.int32) * eligible
            resources = rb * 12 * self.scenario.data_symbols
            mcs = self.mcs_table[rb.long(), self.users[None, :]]
            bits, _feedback, _, tbler, _ = self.phy(
                mcs,
                sinr_eff=self.sinr,
                num_allocated_re=resources,
                mcs_table_index=1,
                mcs_category=1,
            )
            self.previous.zero_()
            arrays = [bits.sum(0), rb.sum(0), (mcs * (rb > 0)).sum(0), (tbler * (rb > 0)).sum(0)]
            return tuple(a.cpu().numpy() for a in arrays)

    def feedback(self, served_bits: np.ndarray) -> None:
        # PF history reflects useful queued bits, including partial packet service.
        self.previous = (
            self.torch.as_tensor(
                served_bits,
                dtype=self.torch.float32,
                device=self.device,
            )[None, :]
            * self.mask
        )
