# Third-party components

This repository contains the benchmark application, installation logic, and the fonts listed below. `install.sh` downloads the remaining third-party components from their upstream publishers and verifies each pinned artifact with SHA-256.

## UI fonts

- Roboto Flex: https://github.com/googlefonts/roboto-flex
- Roboto Mono: https://github.com/googlefonts/RobotoMono
- Noto Sans Japanese: https://github.com/notofonts/noto-cjk
- License: SIL Open Font License 1.1
- Bundled license texts: `static/fonts/OFL-*.txt`

The bundled WOFF2 files are losslessly converted from the variable TTF files published in the Google Fonts repository. They are served locally so the benchmark UI remains visually consistent without internet access.

## Qualcomm GenieX 0.6.1

- Source and releases: https://github.com/qualcomm/GenieX
- License: BSD-3-Clause
- Additional terms: see the Qualcomm Terms of Use referenced by the upstream repository
- Artifact: `geniex-cli-linux-arm64-v0.6.1.tar.gz`

## Qwen3-VL 2B Instruct

- Base model: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct
- Official GGUF projector: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF
- Q4_0 conversion: https://huggingface.co/bartowski/Qwen_Qwen3-VL-2B-Instruct-GGUF
- License declared by the model repositories: Apache-2.0

## Pillow 10.4.0

- Source: https://github.com/python-pillow/Pillow
- License: HPND

## OpenCL ICD loader

- Package: Ubuntu `ocl-icd-libopencl1`
- Source: https://github.com/OCL-dev/ocl-icd
- License: BSD-2-Clause

The model and runtime files are intentionally excluded from Git. Anyone distributing an installation image or derived bundle must retain the required upstream notices and review the terms applicable to that distribution.

## Trademark notice

This is an independent community project and is not affiliated with or endorsed by Qualcomm Technologies, Inc. or Arduino.

Qualcomm, Qualcomm Dragonwing, Hexagon, and Kryo are trademarks or registered trademarks of Qualcomm Incorporated. Arduino and VENTUNO are trademarks of Arduino SA. All product names are used only to identify the hardware and software exercised by this benchmark.
