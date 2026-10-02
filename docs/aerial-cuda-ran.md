# Aerial CUDA-Accelerated RAN for this testbed

Assessed on **2026-10-02**, against NVIDIA release **26-2** and upstream commit **`4f65f97c1d5f701ce911f7dda8f1b1f3f0c7693c`**. This is a feasibility and integration guide. **CUDA-Accelerated RAN is not installed here, and no pyAerial or cuMAC result has been validated on this PC.** The implemented experiments still use Sionna RT + SYS.

## Which parts would help?

[CUDA-Accelerated RAN](https://github.com/NVIDIA/aerial-cuda-accelerated-ran) is a gNB development SDK. It supplies radio processing and scheduling components; it does not import our Ottawa terrain and produce application traffic results automatically.

| Component | Experiment it could support | Work still required here |
| --- | --- | --- |
| pyAerial / cuPHY | NR uplink/downlink signal processing, coding and modulation, receiver comparisons, measured transport-block errors | Validate the kernels on the host, then bridge Sionna channels to the signal-processing pipeline. |
| cuMAC | UE selection and allocation of radio resources; comparing scheduling algorithms | Adapt channel, buffer, UE and cell state to cuMAC inputs; apply its decisions to our simulated radio/packet queues. |
| Full gNB SDK | Integration with an L2 stack and fronthaul for RAN development | A supported Linux deployment and the other protocol components appropriate to the experiment. |

pyAerial exposes a subset of cuPHY through Python, including PDSCH/PUSCH functionality. NVIDIA supplies a [PUSCH link-simulation example](https://docs.nvidia.com/aerial/cuda-accelerated-ran/latest/content/notebooks/example_pusch_simulation.html) with native TDL/CDL channels and fused/separable receiver comparisons. This is an appropriate first validation target. [pyAerial overview](https://docs.nvidia.com/aerial/cuda-accelerated-ran/latest/pyaerial/overview.html).

cuMAC provides CUDA scheduling algorithms for integration into L2. Service priority needs an explicit policy and suitable input state; the presence of cuMAC alone does not establish sensor QoS or a complete packet protocol. [cuMAC overview](https://docs.nvidia.com/aerial/cuda-accelerated-ran/latest/cubb/cumac/overview.html).

## Can this PC run it?

Local checks found Windows 11 Enterprise, two **8 GB RTX 3070s**, compute capability **8.6**, driver **591.86**, and no installed WSL or Docker. Each GPU has its own memory; two cards do not provide one 16 GB allocation.

The full release-26-2 gNB deployment lists MGX ARC Pro, Grace Hopper MGX and DGX Spark systems. Its manifest specifies Linux platform software, CUDA 13.3 and platform-specific driver/NIC dependencies. This workstation is outside that deployment matrix. [Software manifest](https://docs.nvidia.com/aerial/cuda-accelerated-ran/latest/install_guide/software_manifest.html).

An **experimental pyAerial build** is more plausible than a full gNB deployment. NVIDIA documents a CUDA architecture override, but says pyAerial is tested against compute capabilities 8.0 and 9.0 and gives no correctness guarantee for other GPUs. Target `86` for these RTX 3070s; that argument is inferred from their measured architecture, not a qualification claim. Linux/container GPU access, driver compatibility and available VRAM must all pass actual tests. [pyAerial installation](https://docs.nvidia.com/aerial/cuda-accelerated-ran/latest/pyaerial/getting_started.html).

Do host provisioning separately from the running calibrated campaign. Enabling WSL or changing the GPU driver may require a restart. Keep the SDK, its dependencies and its GPU jobs separate from this project's `.venv` and active supervisor.

## Proposed installation and validation

These commands have **not been executed here**. First provision a Linux environment with working GPU-enabled Docker, obtain the matching SDK container through NVIDIA's [installation guide](https://docs.nvidia.com/aerial/cuda-accelerated-ran/latest/install_guide/index.html), and verify GPU visibility inside that container. WSL would be an experimental development route, not the supported full gNB platform.

In the Linux environment, record and pin the source:

```bash
git clone https://github.com/NVIDIA/aerial-cuda-accelerated-ran.git
cd aerial-cuda-accelerated-ran
git checkout 4f65f97c1d5f701ce911f7dda8f1b1f3f0c7693c
git rev-parse HEAD
./cuPHY-CP/container/run_aerial.sh
```

Inside the matching standard Aerial container:

```bash
cd "$cuBB_SDK"
cmake --preset pyaerial-x86 -DCMAKE_CUDA_ARCHITECTURES="86"
cmake --build --preset pyaerial-x86 --target pyaerial_setup
source pyaerial/.venv/bin/activate
python3 -c "import aerial"
```

Obtain/generate the matching test vectors and set `TEST_VECTOR_DIR` to their container-visible directory before configuring/testing when using a custom location. Then run:

```bash
cmake --build --preset pyaerial-x86 --target pyaerial_test
```

These use the current [26-2 build workflow](https://github.com/NVIDIA/aerial-cuda-accelerated-ran/blob/4f65f97c1d5f701ce911f7dda8f1b1f3f0c7693c/pyaerial/README.md); older separate pyAerial-container instructions are not the recipe for this checkout. A successful import proves only that bindings load. Require passing reference tests and a reproducible PUSCH simulation before accepting scientific outputs. Archive source revision, image digest, driver/runtime versions, GPU model, configuration, seeds and test logs.

## Connecting our terrain and traffic experiments

The intended data flow is:

```text
Sionna RT terrain + BS/UE positions -> complex radio channels
                                      |
                               pyAerial / cuPHY
                                      |
                       decoded blocks / measured error rates
                                      |
              packet queues + scheduler (optionally cuMAC) -> metrics
```

Integration should proceed in this order:

1. Reproduce the official channel-model link example on one GPU, starting with a small slot budget. Check low/high-SNR behavior and decoded payloads. Record uncertainty; a small run is a functional check.
2. Replace its channel application with Sionna complex path gains and delays. Validate antenna-port ordering, subcarrier spacing, FFT/resource-grid layout, timing, DMRS and noise/power units. A saved RSRP map alone cannot supply a waveform or a MIMO channel.
3. Compare identical channel realizations under fixed MCS values and receiver configurations. Report modulation **and coding rate**, direction, bandwidth, antenna configuration and enough transport blocks to interpret BLER.
4. Feed validated outcomes, or channel-specific error models derived from them, into the packet experiment layer. Add cuMAC separately for scheduler comparisons, using the same UE placements, offered traffic, channel inputs and seeds.

Our [existing uplink/modulation guide](uplink-modulation-experiments.md) describes the working SYS abstraction and its assumptions. The proposed cuPHY integration would add decoded-signal evidence; it has not replaced that implementation.

## Results and calibration limits

| Result | Required evidence |
| --- | --- |
| BLER / BER | Actual decoded blocks/bits against transmitted data; sufficient trials per channel/MCS. |
| Bandwidth and resource allocation | Configured carrier bandwidth plus scheduled PRBs/time; these differ from achieved data rate. |
| Throughput / goodput | Explicit counting of transmitted, decoded and completed payload bits per simulated second. |
| Packet loss and latency | Packet queues, retry/drop rules, timestamps and any modeled MAC/RLC/core/application behavior. Report queued packets at the end of the minute. |
| GPU processing time | Separate wall-clock/kernel measurements; these are not automatically network delivery latency. |

Mobile-node data calibrate received-power behavior in the existing campaign. They do not validate modulation errors, uplink transmit-power control, scheduling delay or end-to-end sensor latency. Preserve held-out power validation and validate any channel conversion. Do not transfer the downlink RSRP correction to uplink without appropriate measurements. See [calibration and campaign evidence](experiment-campaigns.md).

For this PC, the recommended next implementation is an isolated pyAerial feasibility test once Linux/container prerequisites are available. Keep Sionna RT for location/terrain and the packet layer for one-minute sensor experiments. If the RTX 3070 reference tests fail, Sionna PHY remains a NVIDIA alternative for waveform studies, or use a qualified Linux host for Aerial.
