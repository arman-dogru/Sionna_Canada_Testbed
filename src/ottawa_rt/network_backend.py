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
        self.illa = InnerLoopLinkAdaptation(
            self.phy,
            bler_target=scenario.bler_target,
            device=self.device,
        )
        # Static maps allow precomputation for every possible RB allocation.
        counts = torch.arange(scenario.num_rb + 1, device=self.device, dtype=torch.int32)
        resources = counts[:, None].expand(-1, self.num_users) * 12 * scenario.data_symbols
        snr = self.sinr[0].expand(scenario.num_rb + 1, -1)
        with torch.no_grad():
            self.mcs_table = self.select_mcs(snr, resources)
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
                mcs_table_index=scenario.mcs_table_index,
                mcs_category=scenario.mcs_category,
            )
            order, coderate = decode_mcs_index(
                full_mcs,
                table_index=scenario.mcs_table_index,
                is_pusch=scenario.direction == "uplink",
            )
            self.rate = order * coderate * (1 - predicted_tbler) * 12 * scenario.data_symbols
            if scenario.link_adaptation == "fixed" or scenario.direction == "uplink":
                # Forced-MCS experiments must attempt even undecodable grants.
                self.rate = self.rate.clamp(min=1e-6)
        self.users = torch.arange(self.num_users, device=self.device)
        self.last_feedback = np.full(self.num_users, -1)
        self.last_sinr_db = np.asarray(sinr_db)

    def select_mcs(self, sinr, resources):
        if self.scenario.link_adaptation == "fixed":
            return self.torch.full_like(resources, self.scenario.fixed_mcs)
        return self.illa(
            sinr_eff=sinr,
            num_allocated_re=resources,
            mcs_table_index=self.scenario.mcs_table_index,
            mcs_category=self.scenario.mcs_category,
        )

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
            bits, feedback, _, tbler, _ = self.phy(
                mcs,
                sinr_eff=self.sinr,
                num_allocated_re=resources,
                mcs_table_index=self.scenario.mcs_table_index,
                mcs_category=self.scenario.mcs_category,
            )
            self.previous.zero_()
            self.last_feedback = self.torch.where(rb > 0, feedback, 0).sum(0).cpu().numpy()
            self.last_feedback[rb.sum(0).cpu().numpy() == 0] = -1
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


class UplinkBackend(SionnaBackend):
    """Orthogonal in-cell UL grants, with scheduled inter-cell UE interference.

    Fixed UE PSD across the occupied carrier: granting N of B RBs transmits
    Pmax*N/B total power. No 3GPP open/closed-loop power control is assumed.
    Scheduling uses noise-limited rates; decoding uses actual shared-RB SINR.
    """

    def __init__(self, scenario, serving, gain_db):
        from ottawa_rt.geo import thermal_noise_dbm

        occupied_mhz = scenario.num_rb * 12 * 15e3 * 2**scenario.numerology / 1e6
        noise_rb = 10 ** (
            thermal_noise_dbm(occupied_mhz / scenario.num_rb, scenario.noise_figure_db) / 10
        )
        receive_mw = 10 ** ((gain_db + scenario.ue_tx_power_dbm) / 10) / scenario.num_rb
        initial = np.full(len(serving), -100.0)
        for user, cell in enumerate(serving):
            if cell >= 0:
                initial[user] = 10 * np.log10(max(receive_mw[cell, user] / noise_rb, 1e-10))
        super().__init__(scenario, serving, initial)
        if receive_mw.shape != (self.num_cells, self.num_users):
            raise ValueError("Uplink gain matrix must contain each serving cell and UE")
        self.receive_mw = self.torch.as_tensor(
            receive_mw, dtype=self.torch.float32, device=self.device
        )
        self.noise_rb = noise_rb
        self.last_schedule = None

    def step(self, active):
        torch = self.torch
        with torch.no_grad():
            eligible = self.mask & torch.as_tensor(active, device=self.device)[None, :]
            rates = (self.rate * eligible)[:, None, None, :].expand(-1, 1, self.scenario.num_rb, -1)
            scheduled = self.scheduler(self.previous.clamp(min=1e-6), rates)
            grants = scheduled[:, 0, :, :, 0] & eligible[:, None, :]
            self.last_schedule = grants.cpu().numpy()
            rb = grants.sum(1).to(torch.int32)
            # [receiving cell, RB]: only UEs granted this RB transmit.
            transmitting = grants.any(0).to(torch.float32)
            all_power = self.receive_mw @ transmitting.T
            in_cell_power = (self.receive_mw[:, None, :] * grants).sum(-1)
            interference = (all_power - in_cell_power).clamp(min=0)
            per_rb_sinr = self.receive_mw[:, None, :] / (interference[:, :, None] + self.noise_rb)
            # Conservative flat-channel reduction; PF's flat rates currently
            # give each cell's entire carrier to one UE, so all granted RBs
            # have the same SINR. This also bounds future nonuniform grants.
            effective = torch.where(grants, per_rb_sinr, torch.inf).amin(1)
            effective = torch.where(rb > 0, effective.clamp(min=1e-10, max=1e6), 1e-10)
            resources = rb * 12 * self.scenario.data_symbols
            mcs = self.select_mcs(effective, resources)
            bits, feedback, _, tbler, _ = self.phy(
                mcs,
                sinr_eff=effective,
                num_allocated_re=resources,
                mcs_table_index=self.scenario.mcs_table_index,
                mcs_category=0,
            )
            selected = rb > 0
            self.last_feedback = torch.where(selected, feedback, 0).sum(0).cpu().numpy()
            self.last_feedback[rb.sum(0).cpu().numpy() == 0] = -1
            self.last_sinr_db = (
                torch.where(selected, 10 * torch.log10(effective), 0).sum(0).cpu().numpy()
            )
            self.previous.zero_()
            return tuple(
                a.cpu().numpy()
                for a in (
                    bits.sum(0),
                    rb.sum(0),
                    (mcs * selected).sum(0),
                    torch.where(selected, tbler, 0).sum(0),
                )
            )
