# Scripts Guide

Digital Twin(Stage 7) 검증 환경을 기동/관리/확인하는 운영 스크립트. 최초 설치는
[Installation Guide](installation/README.md) 참고.

이 스크립트들은 `docker compose`가 아니라 ONOS 하나만 `docker run --network
host`로 띄우고 Mininet/OVS는 호스트(또는 WSL2)에 네이티브로 설치하는 방식을
씁니다 — 원본(`sdn-intent-framework`)이 검증한 구성 그대로입니다. Mininet은
커널 네트워크 네임스페이스를 직접 다뤄야 해서 컨테이너화의 이득이 적습니다.

## 권장 실행 순서

모든 명령은 저장소 루트에서 실행합니다.

### 1. 환경 상태 확인

```bash
./scripts/installation/doctor.sh
```

Python, uv, Mininet, OVS, Docker와 ONOS 이미지 상태를 확인하고
`logs/setup/`에 환경 보고서를 저장합니다(이 디렉터리는 `.gitignore` 대상).

### 2. ONOS 시작

```bash
./scripts/onos.sh start
```

`tiger-sdn-onos` 컨테이너를 시작하고 REST API가 준비될 때까지 기다린 뒤
OpenFlow 앱을 활성화합니다. 상태 확인: `./scripts/onos.sh status`.

### 3. 기본 연결 Smoke Test

```bash
./scripts/smoke_test.sh
```

단일 OVS switch와 host 3개로 Mininet-OVS-ONOS OpenFlow 1.3 연결과 `pingall`,
ONOS의 장치 인식을 확인합니다. TIGER-SDN 코드는 아직 전혀 관여하지 않는
순수 환경 검증 — 이게 실패하면 twin 검증 전에 먼저 여기부터 고친다.

### 4. Digital Twin Smoke Test

```bash
sudo ./scripts/twin_smoke_test.sh
```

Stage 5 컴파일러로 만든 실제 FlowRule 2개(forward, block)를 Stage 7의
`TwinVerifier`로 이 환경에 배포·검증합니다 — 이 저장소가 twin_verifier.py를
실 ONOS+Mininet에 대고 처음 exercise하는 지점. `tests/test_twin.py`는 순수
로직만 검증하고 이 경로 자체는 커버하지 않는다.

### 5. 수동 Mininet 실험 (선택)

```bash
./scripts/start_mn_single3.sh
```

대화형 Mininet CLI. `pingall`, `net`, `nodes` 등으로 직접 찔러볼 때 사용.

### 6. 실험 종료

```bash
./scripts/onos.sh stop
```

컨테이너는 삭제하지 않고 중지만 한다 — 다음 `start`에서 재사용. 비정상 종료로
잔여 상태가 있으면 `sudo mn -c`.

## 전체 실행 예시

```bash
./scripts/installation/doctor.sh
./scripts/onos.sh start
./scripts/smoke_test.sh
sudo ./scripts/twin_smoke_test.sh
./scripts/onos.sh stop
```

## 스크립트 역할

| Script | Role |
| --- | --- |
| `installation/setup.sh` | 최초 시스템 패키지, uv, Python, ONOS 이미지 설치 |
| `installation/doctor.sh` | 설치 상태, 서비스, 버전, 포트 진단 |
| `onos.sh` | ONOS 시작/중지/재시작/상태/로그 관리 |
| `smoke_test.sh` | Mininet 통신과 ONOS 장치 인식 자동 검증 (TIGER-SDN 코드 무관) |
| `twin_smoke_test.sh` / `twin_smoke.py` | Stage 5 컴파일러 + Stage 7 TwinVerifier를 실 ONOS+Mininet에 대고 검증 |
| `start_mn_single3.sh` | 단일 switch, host 3개 대화형 Mininet 실행 |

ONOS는 TCP 6653(OpenFlow), 8101(SSH/Karaf), 8181(REST) 포트를 씁니다. 기본
계정은 `onos`/`rocks` — `ONOS_USER`/`ONOS_PASSWORD` 환경변수로 재정의 가능.

## WSL2에서 실행하기

Docker Desktop을 쓰는 WSL2 배포판이라면, WSL을 열기 전에 Windows에서 Docker
Desktop을 먼저 켜야 `docker info`가 성공한다. Mininet 관련 명령은 전부 root가
필요하므로 스크립트를 `sudo`로 실행하거나 세션 안에서 `sudo -v`로 미리
인증해 둔다.
