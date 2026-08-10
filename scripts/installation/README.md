# Installation Guide

원본: `sdn-intent-framework`의 `research/scripts/installation/README.md`.
바뀐 점: Ubuntu 22.04(WSL2 기본 LTS)를 24.04와 함께 지원. `safe-intent-onos` ->
`tiger-sdn-onos` 컨테이너 이름 변경 외 절차는 동일.

## 지원 환경

자동화 대상은 Ubuntu 22.04/24.04 LTS x86_64(WSL2 포함)입니다. Mininet과 Open
vSwitch는 호스트(또는 WSL2 배포판)에서, ONOS는 host network를 사용하는 Docker
컨테이너에서 실행합니다 — docker-compose가 아닙니다. Mininet은 커널 네트워크
네임스페이스/OVS를 직접 조작해야 해서 컨테이너 안에 넣으면 얻는 이득이 적고,
ONOS 하나만 컨테이너화하는 편이 원본 레포에서도 검증된 방식입니다.

### 사전 조건

- Ubuntu 22.04 또는 24.04 LTS x86_64 (WSL2도 가능 — `wsl -d <distro>`)
- 인터넷 연결
- `sudo` 사용 권한
- Docker Desktop을 쓰는 WSL2라면, WSL2 배포판을 실행하기 전에 Windows에서
  Docker Desktop을 먼저 켜 둔다 (daemon이 없으면 `docker info`가 실패한다)
- Git과 `curl`

### 저장소가 Windows 파일시스템(`/mnt/c/...`)에 있는 WSL2라면

`uv sync`가 만드는 `.venv`는 OS별 바이너리를 담기 때문에, 저장소 경로를
그대로 두고 WSL 쪽에서도 `uv sync`를 돌리면 Windows에서 이미 만든 `.venv`를
Linux 바이너리로 덮어써 Windows 쪽 개발 환경이 깨집니다. WSL의 네이티브
파일시스템(예: `$HOME`)에 별도 venv를 두도록 지정하세요.

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/tiger-sdn"
```

이 값을 export한 채로 `setup.sh`(및 이후의 `uv run`/`uv sync`)를 실행하면
`doctor.sh`도 이 경로를 인식합니다. `/mnt/c` 위 I/O가 WSL 네이티브 파일시스템보다
느리기도 해서 venv를 밖에 두면 그 이점도 같이 얻습니다.

## 설치

저장소를 받은 뒤 프로젝트 루트에서 설치 스크립트를 실행합니다.

```bash
chmod +x scripts/installation/*.sh scripts/*.sh
./scripts/installation/setup.sh
```

`setup.sh`는 다음 작업을 순서대로 수행합니다.

1. Ubuntu 22.04/24.04 및 x86_64 환경 여부를 확인합니다.
2. `apt`로 Mininet과 Open vSwitch를 설치하고 OVS 서비스를 활성화합니다.
3. Docker가 없다면 Ubuntu의 `docker.io` 패키지를 설치하고 서비스를 활성화합니다.
4. 사용자 영역에 `uv`를 설치합니다(이미 있으면 건너뜀).
5. `pyproject.toml`/`uv.lock`을 기준으로 Python 3.11 가상환경을 동기화합니다.
6. `onosproject/onos:2.7.0` Docker 이미지를 내려받습니다.
7. 설치된 도구와 서비스 버전을 검사하고 `logs/setup/`에 보고서를 저장합니다.

## 설치 검증

```bash
./scripts/installation/doctor.sh
./scripts/onos.sh start
./scripts/smoke_test.sh
./scripts/twin_smoke_test.sh
./scripts/onos.sh stop
```

`smoke_test.sh`가 `PASS: connectivity and ONOS device discovery succeeded.`를
출력하면 Mininet host 통신, OpenFlow 1.3 연결 및 ONOS 장치 인식이 정상입니다.
`twin_smoke_test.sh`는 Stage 5 컴파일러로 만든 실제 FlowRule을 Stage 7의
`TwinVerifier`로 이 환경에 직접 배포해 검증합니다 — 이 프로젝트가 실제 논문
로직을 실 컨트롤러/데이터플레인에 대고 처음 확인하는 지점입니다.

보고서 파일을 생성하지 않고 환경만 검사하려면 다음 명령을 사용합니다.

```bash
./scripts/installation/doctor.sh --no-write
```

## 문제 해결 및 정리

```bash
./scripts/installation/doctor.sh --no-write
./scripts/onos.sh status
./scripts/onos.sh logs
sudo ss -lntp | grep -E ':(6653|8101|8181)\b'

# 비정상 종료된 Mininet namespace와 OVS 상태 정리
sudo mn -c

# ONOS 컨테이너 완전 제거
./scripts/onos.sh stop
docker rm tiger-sdn-onos
```
