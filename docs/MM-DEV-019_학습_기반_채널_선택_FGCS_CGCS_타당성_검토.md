# MM-DEV-019 학습 기반 채널 선택(FGCS/CGCS) 타당성 검토

- 기준 사양: `docs/다중_마이크_녹음_통합_전사_기능_개발_사양서.md` 2절·13절
- 선행 평가: `docs/MM-DEV-017_실제_회의_녹음_CER_WER_평가.md`
- 관련 R&D: `docs/MM-DEV-018_겹침_발화_및_원거리_발화_보완_검토.md`
- 대상 논문: Bingshen Mu 외, *Automatic channel selection and spatial feature integration for multi-channel speech recognition across various array topologies*, ICASSP 2024, [arXiv:2312.09746](https://arxiv.org/html/2312.09746v1), [DOI](https://doi.org/10.1109/ICASSP48485.2024.10447563)
- 작성 기준일: 2026-10-27

## 1. 결론

**현 시점에는 논문의 전체 FGCS/CGCS 시스템을 제품 V2 PoC로 바로 구현하지 않는다.** 정확 재현에 필요한 저자 코드·제안 모델 체크포인트·상세 학습 설정이 공개 자료에서 확인되지 않았고, 논문 시스템은 현재 제품의 휴리스틱 채널 선택기를 대체할 수 있는 독립 전처리기가 아니기 때문이다.

- full system 직접 도입 보류 판단 신뢰도: **높음** — 구조·출력 계약 불일치와 재현 자료 부재가 확인됨
- 제품 호환형 learned hard selector의 후속 PoC 가치: **중간 이하** — 통합 경로는 있으나 허가된 데이터가 없고 겹침 발화의 단일채널 label 충돌 가능성이 큼

논문의 채널 선택은 다음 구성과 공동 학습되는 ASR 내부의 soft attention이다.

아래는 데이터 흐름을 줄인 개략도다. 실제로는 초기 CGCS/GRC와 encoder layer 안의 선택·통합 블록을 구분해야 한다.

```text
여러 장치/배열의 다채널 원음
  ├─ WPE + 장치별 BeamformIt ──► K개 장치 스트림 ──► WavLM ─┐
  └─ oracle 발화 구간 + GSS ───► 목표 단일 스트림 ──► WavLM ─┤
                                                             ▼
                                   encoder 밖 초기 CGCS + GRC → V(0)
                                                             │
                    각 Transformer encoder layer에서 반복:
                    CGCS(이전 layer value) → FGCS
                    → cosIPD 결합 → MFCCA
                                                             │
                                  encoder 뒤 convolution fusion
                                                             │
                                  CTC/Attention decoder + LM
                                                             │
                                      선택적 ROVER 후처리
```

현재 제품은 독립 녹음 장치의 파일을 정렬하고, 3초 창/1초 hop의 음질 특징으로 **한 채널을 hard selection**한 뒤 하나의 WAV를 기존 Whisper에 전달한다. 반면 논문 모델은 GSS 목표 특징을 query로 쓰고 다채널 잠재 특징을 융합하므로 `engine/multimic/select.py`만 교체해서 붙일 수 없다. CHiME-7의 모든 장치가 sample-synchronized된 것도 아니지만, CHiME 데이터 동기 조건과 논문 저자의 WPE/BeamformIt 전처리를 합친 경로가 현재 제품의 독립 장치 drift 보정과 cosIPD 관점에서 동등하다는 근거도 없다.

따라서 다음처럼 범위를 나눈다.

| 판단 대상 | 결정 | 이유 |
|---|---|---|
| 논문 수치의 문헌 검증 | 완료 | 단일 `+ALL` 모델은 Eval Macro DA-WER 33.4 → 22.0, **34.1% 상대 감소**다. 40.1%는 여러 시스템을 ROVER로 합친 20.0 결과다. |
| 논문 전체 시스템의 exact reproduction | 현재 불가 | 제안 모델 코드/체크포인트와 핵심 학습·실행 설정이 없다. CHiME-7 데이터 접근과 대규모 GPU 환경도 필요하다. |
| 논문 전체 시스템의 독립 재구현 | 조건부 연구 가능 | 논문에 큰 구조와 수식은 있으나 구현 선택이 비결정적이므로 같은 수치를 재현한다고 보장할 수 없다. |
| 현재 V1에 직접 삽입 | 기각 | 출력이 채널 구간/WAV가 아니라 공동 학습된 ASR 잠재 특징이다. Whisper를 그대로 유지할 수 없다. |
| 제품 호환형 learned hard selector | headroom gate 뒤 조건부 PoC | 기존 `ChannelSelectionReport` 계약과 Whisper를 유지할 수 있다. 다만 oracle-informed hard-selection probe가 목표 개선 신호를 먼저 보여야 하며, 이는 FGCS/CGCS의 **영감 기반 변형**이지 논문 재현이 아니다. |
| NPU 우선 구현 | 보류 | 현재 시험 장비에 NPU가 없고, 가변 길이·가변 채널 ASR 그래프의 NPU 배치가 검증되지 않았다. CPU/GPU 기준을 먼저 만든다. |

### 완료 조건 대응

| 티켓 완료 조건 | 문서 위치 |
|---|---|
| FGCS/CGCS 및 공간 특징 통합 요구사항 | 2절 |
| 재현 가능 범위 | 3절 |
| 요구 데이터와 예상 종속성 | 4~5절 |
| 학습/추론 런타임·CPU/NPU 비용 | 6절 |
| 현재 휴리스틱과 공정한 비교 실험 | 7절 |
| PoC 진입·중단·성공 기준 | 8절 |
| 프로토타입 구현 여부 판단 | 8~10절 |

## 2. 논문 모델 요구사항

### 2.1 입력 전처리와 목표 신호

논문은 CHiME-7 acoustic robustness sub-track의 CHiME-6, DiPCo, Mixer 6을 사용한다. 각 데이터의 장치 구성은 각각 6×4, 5×7, 10×1 마이크이고, 논문이 보고한 processed training set 합계는 276시간이다. DiPCo는 공식 train split이 없으므로 이 수치를 세 corpus의 독립 raw 녹음 시간 합계로 해석하지 않는다.

1. 각 배열의 내부 마이크에 WPE를 적용한다.
2. 배열별 BeamformIt으로 배열 하나를 단일 스트림 하나로 줄인다. 결과 채널 수 `K`는 배열/장치 수다.
3. oracle 발화 segmentation으로 GSS를 실행해 utterance별 목표 단일 스트림을 만든다.
4. WPE+BeamformIt의 `K`개 스트림과 GSS 목표 스트림을 모두 16 kHz WavLM 입력으로 사용한다.

즉, GSS 신호는 단순 학습 label만이 아니라 CGCS/FGCS attention의 query를 만드는 입력이다. 실제 제품에서 oracle 발화 경계와 GSS query 없이 같은 계산 그래프를 실행하려면 별도의 segmentation/GSS 정책을 정의해야 한다.

### 2.2 CGCS와 GRC

CGCS(coarse-grained channel selection)는 utterance 전체의 의미 정보량을 기준으로 채널 가중치를 학습한다.

- 채널 수준 AFE(audio feature extractor): 4층 양방향 GRU, CTC loss로 사전 학습된 ASR에서 초기화
- query: GSS 목표 음성의 의미 특징
- key: WPE+BeamformIt 채널별 AFE 특징
- encoder 밖 초기 CGCS의 value: WPE+BeamformIt 채널별 WavLM frame 특징
- 출력: 각 채널을 제거하는 hard mask가 아니라 attention으로 가중된 `B × K × T × D` 특징
- GRC(gated residual connection): 원 특징을 무조건 residual add하지 않고 학습된 gate로 다시 섞는다.

초기 CGCS+GRC가 만든 `V(0)` 뒤에는 각 Transformer encoder layer 안에서 CGCS가 반복되며, 이때 value는 이전 layer 출력이다. 이어 FGCS, cosIPD 결합, MFCCA가 적용되고 encoder 뒤 convolution fusion이 채널 축을 줄인다.

CGCS는 제품 V1처럼 설명 가능한 `selected_input_index` 하나를 내지 않는다. 논문의 attention 시각화는 채널별 중요도를 보여 주지만, 합성 WAV의 구간 선택 이력과 동일한 것은 아니다.

### 2.3 FGCS

FGCS(fine-grained channel selection)는 frame마다 GSS 특징과 각 채널 특징의 유사도를 attention으로 계산한다.

- query: GSS 목표 음성의 WavLM frame 특징 `B × 1 × T × D`
- key/value: CGCS 출력 `B × K × T × D`
- 출력: frame별로 다시 가중된 다채널 잠재 특징

저자는 ablation 설명에서 FGCS가 가장 큰 개선을 보였다고 서술한다. 다만 최종 수식에서 FGCS의 key/value는 CGCS 출력을 사용하고 표의 `+FGCS` checkpoint 조합은 미기재이므로, FGCS만의 독립적인 인과 효과로 분리할 수 없다. 이 결과는 GSS query, WavLM, MFCCA와 해당 ASR 학습 목표를 포함한 조건이며 일반적인 음질 분류기 성능으로 이전할 수 없다.

### 2.4 MFCCA, cosIPD, convolution fusion

- MFCCA(multi-frame cross-channel attention)는 인접 frame과 채널의 상관을 함께 학습한다.
- cosIPD는 32 ms window, 16 ms frame shift, 512-point STFT로 계산하고 GRU 기반 CFE를 거쳐 FGCS 출력에 결합한다.
- U-Net형 convolution fusion은 skip connection과 다중 scale 특징을 이용해 채널 축을 한 채널 특징으로 줄인다.
- fusion의 입력 채널 수 `C`는 10으로 고정하고, 실제 `K < 10`이면 채널을 반복해 채운다.

논문의 topology-agnostic 주장은 위 세 CHiME scenario에서의 결과다. 채널 반복은 tensor 폭을 맞추는 구현 규칙이지 임의 topology 일반화의 증거가 아니며, `K=2` 제품 입력은 평가되지 않았다.

cosIPD는 전처리 뒤 채널 사이의 위상 관계가 의미 있게 유지된다는 전제가 필요하다. CHiME-7도 모든 장치가 sample-synchronized된 데이터는 아니며, 논문 저자는 별도의 WPE/BeamformIt 전처리를 사용한다. 현재 제품은 서로 다른 소비자 장치의 offset과 clock drift를 자체 경로로 사후 보정하고 AAC 같은 손실 압축도 허용하므로, CHiME 데이터 동기 조건과 논문 전처리를 합친 경로가 제품 경로와 동등하다고 다음 검증 없이 가정하지 않는다.

현재 decode 경로는 파일 하나를 16 kHz mono로 downmix하므로, 한 파일 안의 배열 채널 위상 정보도 제품 파이프라인에 들어오지 않는다. 논문형 공간 특징을 시험하려면 배열 원본을 보존하는 별도 연구 decode 경로가 필요하다.

- 동일 음원을 장치별로 녹음했을 때 주파수별 IPD가 시간에 따라 안정적인지
- affine drift 보정 뒤에도 32 ms window 안의 위상 관계가 유지되는지
- 장치별 AGC, codec delay, resampling이 cosIPD 분포를 깨지 않는지
- 위상 특징을 뺀 모델보다 held-out CER/WER를 실제로 개선하는지

### 2.5 ASR와 보고 성능

제안 모듈은 다음 ASR와 공동 사용된다.

- frozen WavLM Large frontend
- 12층 Transformer encoder, 6층 Transformer decoder
- MHSA 차원 256, FFN 차원 2048
- hybrid CTC/Attention 학습
- CHiME-7+LibriSpeech 텍스트로 학습한 Transformer LM 재채점

논문 표의 Eval Macro DA-WER는 다음과 같다. Macro 값은 CHiME-6·DiPCo·Mixer6 세 scenario의 DA-WER를 동등 가중 평균한 값이며, 전체 단어를 한꺼번에 pooling한 WER가 아니다.

| 시스템 | Eval Macro DA-WER | baseline 대비 상대 변화 |
|---|---:|---:|
| CHiME-7 baseline | 33.4 | 기준 |
| MFCCA | 30.4 | −9.0% |
| `+CGCS` | 26.8 | −19.8% |
| `+GRC` | 25.7 | −23.1% |
| `+FGCS` | 25.6 | −23.4% |
| `+cosIPD` | 26.4 | −21.0% |
| `+U-NetFusion` | 26.7 | −20.1% |
| `+ALL` 단일 모델 | 22.0 | **−34.1%** |
| ROVER | 20.0 | **−40.1%** |

`+ALL` 이전 행이 모두 하나의 누적 실험이라는 뜻으로 해석하지 않는다. 논문의 표와 설명에 따른 ablation label일 뿐이며, 정확한 checkpoint 조합은 구현 자료가 필요하다. ROVER는 channel feature fusion이 아니라 여러 ASR의 word hypothesis를 정렬·투표하는 post-ASR 후처리다. 저자 표현상 “앞서 언급한 모든 ASR 시스템”에 적용했지만 checkpoint 목록·순서·가중치는 미기재다. 따라서 40.1%는 FGCS/CGCS 하나의 효과도, 현재 V1 대비 효과도, 단일 모델의 효과도 아니다.

## 3. 재현 가능 범위

### 3.1 공개 자료로 확정 가능한 범위

다음은 논문과 공개 CHiME/ESPnet 자료로 재현 사양을 고정할 수 있다.

- 데이터 집합 이름, processed train sets 276시간 보고, oracle segmentation 사용
- `WPE → 배열별 BeamformIt → K개 stream` branch와 `oracle segmentation → GSS → target stream` branch의 병렬 전처리 흐름
- CGCS/FGCS의 큰 tensor 관계와 attention 수식
- GRC, MFCCA+cosIPD, U-Net형 fusion의 역할
- WavLM+12/6층 Transformer ASR의 큰 구성
- cosIPD의 window/hop/STFT 크기
- 조건별 DA-WER 표와 ROVER 포함 여부
- CHiME-7 baseline ESPnet recipe와 공개 baseline checkpoint를 smoke-test 출발점으로 사용할 수 있음

### 3.2 exact reproduction을 막는 항목

2026-10-27 기준으로 논문 본문, 제목 검색, 저자 연구실 공개 저장소에서 제안 시스템의 공식 코드나 checkpoint 링크를 확인하지 못했다. 이는 비공개 구현이 없다는 뜻이 아니라, 제3자가 현재 확보한 자료만으로 동일 결과를 재현할 수 없다는 뜻이다.

논문에도 다음 정보가 충분히 기재되지 않았다.

- AFE/CFE hidden size, projection 상세, attention head 수와 dropout
- 공개된 큰 블록 순서를 코드로 옮길 때 필요한 세부 module ordering, projection과 parameter sharing
- GRC와 U-Net fusion의 kernel/stride/channel 수 전체
- optimizer, learning-rate schedule, batch 구성, epoch, seed, checkpoint 선택법
- channel padding/repetition 순서와 mask 처리
- 학습 시 GSS/WPE+BF feature caching 여부
- 제안 모델의 parameter 수, FLOPs, peak memory, 학습 장치/시간
- 추론 RTF, CPU/GPU/NPU 측정값
- ROVER에 실제로 넣은 checkpoint 목록과 투표 설정

선행 CHiME 2023 workshop 문서인 [*The NPU System for DASR Task of CHiME-7 Challenge*](https://www.isca-archive.org/chime_2023/mu23_chime.html)와 최종 ICASSP 논문은 cosIPD를 결합하는 위치 등 구조 설명이 일부 다르다. 여기서 NPU는 저자 소속인 Northwestern Polytechnical University의 약자이며 Neural Processing Unit을 뜻하지 않는다. 재구현 기준은 최종 ICASSP/arXiv v1로 고정한다.

따라서 독립 구현 결과가 논문 수치와 다를 때 구현 오류인지 미기재 설정 차이인지 구분할 수 없다. 저자 코드 또는 최소한 config/checkpoint를 받기 전에는 결과를 “논문 재현”이 아니라 “논문 구조의 독립 재구현”으로 표시한다.

공개 ESPnet recipe/Hugging Face baseline도 논문 Table 1의 baseline과 동등한 anchor가 아니다. 공개 recipe가 보고한 Dev Macro DA-WER는 31.8(GSS, EV top-80%)인 반면 논문 baseline Dev 값은 28.8이고 LM 재채점 등 조건 차이가 있다. 따라서 공개 checkpoint는 경로 smoke test에 사용하되 논문의 28.8/33.4 checkpoint를 재현했다고 표시하지 않는다.

### 3.3 재현 수준 정의

| 수준 | 정의 | 현재 가능 여부 |
|---|---|---|
| R0 문헌 검산 | 표의 절대/상대 수치와 전제 확인 | 가능, 이 문서에서 완료 |
| R1 baseline 재실행 | 공개 ESPnet CHiME-7 baseline을 oracle segmentation으로 실행 | 데이터 라이선스·GPU 확보 후 Dev에서 가능 |
| R2 구조 재구현 | 논문 수식에 맞춘 CGCS/FGCS/MFCCA 구현과 자체 config 공개 | 가능하나 exact reproduction 아님 |
| R3 논문 결과 재현 | 저자 코드/config/checkpoint로 동일 split·scoring에서 결과 검증 | 저자 자료와 Eval 채점 접근 부재로 현재 차단 |
| P1 제품 호환형 학습 선택기 | V1 출력 계약을 유지하는 별도 learned hard selector | 가능하나 논문 재현 아님 |

## 4. 요구 데이터

### 4.1 논문 계열 재현 데이터

| 데이터 | 역할 | 접근/제약 |
|---|---|---|
| CHiME-6/CHiME-5 | 6개 Kinect 배열, 가정 내 회의 | 신호·전사는 라이선스가 필요하며 상업/비상업 조건이 다르다. [CHiME-6 안내](https://chimechallenge.github.io/chime6/download) |
| DiPCo | 5개 7채널 원거리 배열의 dinner-party 회의 | 공개 배포 조건을 확인하고 CHiME recipe와 같은 split을 사용한다. |
| Mixer 6 Speech | 10개 이종 마이크 인터뷰 | CHiME-7용 train/dev partition은 challenge 당시 별도 신청으로 제공됐고 일반 LDC2013S03 상품과 동일하다고 가정할 수 없다. 현재 challenge-specific 접근 가능 여부를 다시 확인하고, Eval reference 비공개도 별도로 취급한다. [CHiME-7 data](https://www.chimechallenge.org/challenges/chime7/task1/data), [LDC 카탈로그](https://catalog.ldc.upenn.edu/LDC2013S03) |
| LibriSpeech text | Transformer LM 학습 보강 | 논문과 동일 text normalization·vocabulary가 필요하다. |
| oracle utterance segmentation | GSS와 acoustic robustness sub-track 조건 | 자동 VAD/diarization 결과로 바꾸면 논문과 다른 조건이다. |

논문과 같은 full reproduction에는 영어 transcript, 화자/발화 시간, 배열·채널 grouping, 공식 recipe가 기대하는 원음과 동기화 metadata가 필요하다. 공개 [ESPnet CHiME recipe](https://github.com/espnet/espnet/tree/89ebca463c544dfaa19e5f76ad5f615f473f6957/egs2/chime7_task1)는 baseline 출발점으로 쓸 수 있지만 제안 모듈은 포함하지 않는다. CHiME-7 공식 submission은 종료됐고 Eval reference가 공개되지 않았으므로, organizer 채점 접근이나 저자 출력 없이는 논문의 Eval 33.4/22.0/20.0을 독립적으로 exact 검증할 수 없다.

### 4.2 제품 도입 판단용 한국어 데이터

영어 CHiME-7 결과만으로 한국어 Whisper 제품의 도입을 결정하지 않는다. 다음은 **PoC 계획 최소치**이며 논문이 보장한 표본 크기가 아니다.

| split | 최소 계획량 | 조건 |
|---|---:|---|
| train | 50시간, 40개 이상 독립 회의 | 2~4개 독립 장치, 10개 이상 공간, 20개 이상 장치 배치, 원본 PCM 우선 |
| dev | 5시간, 10개 이상 독립 회의 | train과 회의·공간·주요 화자 분리, hyperparameter와 threshold 선택 전용 |
| blind test | 5시간, 10개 이상 독립 회의 | dev 결정 뒤 한 번만 개봉, train/dev와 회의·공간·화자 분리 |

모든 split에 다음 metadata를 남긴다.

- 참여자 평가/학습 사용 동의와 보관 근거
- 장치 모델, 위치, 화자와의 거리, sample rate, codec, AGC 여부
- 원본 파일 SHA-256, 시작 offset, drift 보정값과 정렬 신뢰도
- 사람이 작성한 한국어 전사, 기준 시간축 발화 경계와 화자
- `overlap`, `far_field`, `reverb`, `noise`, `codec`, `device_near_speaker` 태그
- 겹침·원거리 짧은 발화 각각 dev와 test에 30건 이상(MM-DEV-018과 같은 탐색 하한)

blind test를 열기 전에 tag와 누락의 운영 정의를 dataset schema에 고정한다.

- `overlap`: 서로 다른 화자의 reference 발화 시간이 1 sample이라도 겹치는 두 발화 모두. 별도 민감도 분석에서 겹침 200 ms 이상도 보고한다.
- `far_field`: 발화 화자와 가장 가까운 녹음 장치의 실측 거리가 2.0 m 이상. 거리 기록이 없거나 위치가 움직여 판정할 수 없는 발화는 confirmatory 집계에서 제외하고 수를 보고한다.
- 누락 발화: MM-DEV-017과 같은 정규화·전역 문자 alignment에서 해당 reference 발화의 일치 문자가 reference 문자 수의 50% 미만인 경우. 빈 reference 발화는 평가 데이터에 넣지 않는다.
- 삽입률: 같은 전역 문자 alignment의 insertion 문자 수를 reference 문자 수로 나눈 값. `clean/non-overlap`은 `overlap` tag가 없는 발화 집합이다.
- 한 발화가 `overlap`과 `far_field`를 동시에 만족하면 두 집계에 모두 포함하되 각 tag의 분모를 별도로 보고한다.

제품 호환형 hard selector의 발화별 중간 label은 train split에서만 다음처럼 만든다.

1. 모든 입력을 동일 Whisper 설정으로 전사한다.
2. 사람 정답에 대한 발화별 CER이 가장 낮은 입력을 oracle channel label로 만든다.
3. 최저 문자 오류 수와 차점의 차이가 1자 이하이거나, dev 개봉 전에 정한 CER 차이 margin(초기안 1.0%p) 미만이면 `ambiguous`로 두고 강제 label에서 제외한다.
4. dev/test에는 oracle label을 모델 입력으로 사용하지 않고 평가·진단에만 쓴다.

발화 label을 3초 window의 one-hot label로 바로 복사하지 않는다. 겹침 화자들이 서로 다른 채널을 선호하면 같은 window에 양립할 수 없는 label이 생기고, 단일 hard output은 두 채널을 동시에 보존할 수 없기 때문이다. 다음 oracle-informed headroom probe를 train/dev에서 먼저 만든다.

1. reference/VAD 경계를 합쳐 active 발화 집합이 일정한 atomic interval을 만들고, 채널별 단일 입력 전사의 문자 오류를 active 발화 전체에 합산한다.
2. 같은 interval의 active 발화별 최적 채널이 충돌하고 어떤 한 채널도 최저 aggregate loss margin 안에 들지 않으면 `multi_channel_conflict`로 표시한다. 이 interval도 전체 timeline 경로에는 aggregate loss가 가장 낮은 채널로 포함하되 one-hot 학습 label에서만 제외한다.
3. 모든 interval을 3초 window/1초 hop에 투영하고, V1과 같은 minimum hold와 허용 switch/boundary 제약 아래 aggregate loss가 낮은 top-N 단일 채널 경로(초기 `N=20`)를 dynamic programming으로 구한다.
4. dev에서 top-N 경로와 V1·constant-channel 경로를 모두 실제 WAV로 렌더링해 동일 Whisper로 다시 전사한다. 사전 등록한 CER·누락·삽입 목적함수로 가장 좋은 feasible 결과를 `P-OH`로 기록하고, 선택 정책을 train label 생성에 고정한다.

Whisper의 절단·연결 문맥 효과 때문에 이 유한 후보 탐색은 수학적 upper bound가 아니며, 실패가 모든 hard selector의 불가능을 증명하지 않는다. 다만 MM-DEV-018의 통제 실험에서 overlap 누락이 V1 39/96에서 사후 best single도 38/96으로만 줄어 단일채널 선택만으로 30% 개선할 근거가 없었다. 따라서 dev의 각 tag에 reference 30건 이상과 `M_V1 >= 10`이 있을 때 P-OH가 8.3절의 tag별 누락 목표와 clean/삽입 안전 기준을 동시에 충족하지 못하면 **보수적 제품 의사결정으로** learned hard selector 학습을 중단한다. 표본 조건이 부족하면 실패가 아니라 판정 불가로 두고 dev 데이터를 추가한다. 팀이 더 넓은 탐색이나 다른 hard 정책을 승인할 때만 별도 사전 등록 뒤 재개한다. 이 label은 Whisper 설정과 제약 정책에 종속되므로 모델, decoding 또는 선택 정책을 바꾸면 다시 만든다.

## 5. 예상 종속성과 구현 경계

### 5.1 별도 연구 환경

논문 계열 실험은 현재 `engine/.venv`에 패키지를 추가하지 않고 Linux 컨테이너/별도 환경으로 격리한다. 예상 항목은 다음과 같다.

- CUDA 지원 PyTorch, ESPnet/ESPnet2, S3PRL 또는 동일 WavLM frontend
- WavLM Large checkpoint
- CHiME-7 ESPnet recipe, SentencePiece/BPE, Transformer LM
- WPE(`nara_wpe` 계열), BeamformIt, GSS와 관련 CHiME 도구
- Kaldi/SoX/Lhotse 등 공식 recipe가 호출하는 데이터 준비 도구
- pinned CHiME-7 `local/da_wer_score.py`; MeetEval은 동일 입력에서 결과 동등성을 검증한 뒤에만 대체 허용
- 실험 추적 도구와 config/seed/environment lock

공개 baseline model card의 역사적 재현 시작점은 Python 3.9.2, ESPnet `202301`, PyTorch 1.13.1, ESPnet commit `89ebca463c544dfaa19e5f76ad5f615f473f6957`이다. 최신 버전으로 임의 업그레이드하지 말고 먼저 이 환경을 컨테이너 digest와 lock file로 고정한 뒤, 보안·장치 사유로 버전을 바꿀 때 결과 차이를 별도 기록한다.

2026-10-27 조회 기준 공개 baseline repository revision은 `9f28e8a021e54e9d4d667c14e0255ea90cb6d701`, 1,383,608,373-byte weight의 LFS SHA-256은 `a18555e7ca332b5d4032d843db3284c508e49fdc14ad913e8de82920594c49a2`다. 실제 재실행 때 이 revision과 hash를 다시 검증한다.

현재 제품 `engine/requirements.txt`에는 OpenVINO 계열(`openvino`, `openvino-genai`, `openvino-tokenizers`), `huggingface-hub`, PyAV, NumPy, sherpa-onnx가 있지만 PyTorch/ESPnet/WavLM/GSS는 없다. 연구 의존성을 제품 환경에 섞으면 설치 크기, CUDA DLL, 라이선스, 시작 시간과 지원 범위가 크게 바뀐다.

### 5.2 현재 V1과의 계약 차이

| 항목 | 현재 V1 | 논문 시스템 |
|---|---|---|
| 선택 단위 | 3초 창, 1초 hop 뒤 연속 hard 구간 | utterance/channel 및 frame soft attention |
| 특징 | valid/speech/SNR/clipping/gap/noise | WavLM semantic feature, GSS similarity, cosIPD |
| 안정화 | 0.05 hysteresis, 2.5초 hold, 0.5초 발화 경계 snap | 별도 hard switch 없음 |
| 출력 | `ChannelSelectionReport`, 하나의 16 kHz mono WAV | 융합된 ASR latent feature/토큰 |
| downstream | 기존 OpenVINO Whisper 그대로 | 별도 WavLM+Transformer ASR 공동 학습 |
| 설명/override | 구간별 이유·confidence·사용자 override | attention score 외 제품 계약 없음 |
| 입력 topology | 독립 장치 mono 파일, 제품 고유 offset/drift 보정 | 다채널/다배열 CHiME 입력, 공식 recipe 정렬·전처리 |

제품 호환형 PoC는 `ChannelSelectionReport`와 렌더러·manual override·무음 padding 계약을 유지하되, 단순히 점수 함수 하나만 바꾸는 작업으로 보지 않는다. 현재 `plan_multi_audio_selection()`은 VAD, 휴리스틱 품질 분석, `select_channels()`를 직접 연결하고, 선택기는 VAD가 음성으로 인정한 채널을 먼저 후보로 제한하며 후보가 하나면 점수/hold 비교를 우회한다. 이 상태에서는 learned scorer가 원거리 발화를 살리고 싶어도 VAD 단계에서 후보가 제거될 수 있다.

따라서 P-LS 구현 전 다음 경계를 먼저 만든다.

- private 함수 `_score_window_input()`을 외부 계약으로 쓰지 않고, versioned feature extractor와 scorer injection point를 만든다.
- 휴리스틱 `QualityScoringConfig`와 learned model metadata를 분리하고, `method`, checkpoint SHA-256, feature schema, calibration version, inference device를 결과에 기록한다.
- learned score를 `0..1` confidence, `reason`, `selection_source`를 요구하는 기존 C++/GUI parser 계약에 맞추는 adapter를 둔다.
- VAD 후보 제한을 유지한 조건과 완화한 조건을 ablation하여, scorer 개선과 VAD recall 개선을 분리한다.
- 안정화 정책은 동일하게 유지한 주 비교와 learned score에 맞춰 dev에서 다시 보정한 부 비교를 나눈다.

현재 manifest는 자동 선택 한 종류만 표현하고 MM-DEV-017 runner도 단일 회의의 `multi` 조건 하나를 기준으로 발화 bootstrap을 수행한다. P-LS 구현 시에는 별도 PoC CLI에서 위 metadata를 기록하고, 평가 runner가 `multi_v1`과 `multi_learned`를 같은 실행에 포함하도록 확장해야 한다. 최종 도입 판정 전에 여러 회의를 묶는 paired scorer와 condition별 selection artifact 매핑도 추가한다. 이 접근은 제품 계약과 Whisper를 유지하지만 FGCS/CGCS와 동일한 모델은 아니다.

## 6. 비용 산정

### 6.1 현재 기준선 실측

MM-DEV-017의 로컬 `artifacts/mm-dev-017/sample_20261026/20261026_baseline/run.json` 산출물을 다시 확인한 결과는 다음과 같다.

- 호스트: AMD Ryzen 7 260, 8 core/16 thread
- 2026-10-27에 별도 조회한 OpenVINO 장치: CPU, NVIDIA GeForce RTX 5060 Laptop GPU; **NPU 없음**
- 입력: 약 3,628초, 독립 장치 2개
- 다중 입력의 decode→align→VAD→quality→select→render 합계: 46.321초
- 위 합계에 `preparing` 2.016초를 포함한 front-end: 48.337초, RTF 약 0.0133
- 전체 multi 실행: 3,591초, 전체 RTF 약 0.99
- 그중 Whisper 전사 2,059초, CPU diarization 1,478초

stage 합계는 `conditions[name=multi].stages[].seconds`에서 위 이름의 여섯 단계를 더했다. 하드웨어 목록은 산출물에 완전히 저장돼 있지 않으므로 다음 명령의 당일 결과와 구분한다.

```powershell
engine\.venv\Scripts\python.exe -c "from openvino import Core; c=Core(); print(c.available_devices); print({d: c.get_property(d, 'FULL_DEVICE_NAME') for d in c.available_devices})"
```

이 값은 현재 장비·샘플 한 건의 기준선이지 일반 성능 보장은 아니다. 다만 learned front-end가 제품 전체 시간에 추가하는 비용을 같은 장비에서 비교하는 기준으로 쓴다.

### 6.2 저장 공간

논문이 보고한 processed 276시간을 모두 16 kHz mono PCM16 한 stream으로 materialize한다고 가정하면 약 31.8 GB다. 이를 각 topology의 `K=5~10` 채널과 GSS 한 채널에 단순 적용하면 약 191~350 GB다. 276시간의 구성은 공개되지 않았고 raw corpus 시간과 같지 않으므로 이는 저장 공간 산정을 위한 가정이지 원시 데이터 실측이 아니다. 실제 작업 공간에는 장치당 4~7개 마이크 원음, WPE/GSS 중간 산출물, feature cache와 여러 실험 checkpoint가 추가된다.

- 논문 계열 full data 작업 공간: **1~2 TB 권장**
- 60시간 한국어 PoC, 최대 4장치, 48 kHz PCM 원본과 중간 산출물: **0.2~0.5 TB 권장**
- checkpoint: 공개 CHiME-7 baseline artifact가 약 1.38 GB이며, 실험별 optimizer/checkpoint 보존 시 수십~수백 GB

공식 최신 CHiME ESPnet 계열 recipe도 전체 데이터 준비에 약 1 TB를 안내한다. 데이터 구성이 동일하지 않으므로 여기서는 규모 확인용 참고로만 쓴다.

### 6.3 학습 비용

논문은 제안 모델의 장치, 학습 시간, peak VRAM을 보고하지 않았다. 공개 baseline metadata에는 WavLM Large, FP32, WavLM freeze, 최대 8 epoch, 분산 world size 5가 기록돼 있다. 이를 바탕으로 한 **ROM(rough order-of-magnitude, 오차 ±3배) 예산**은 다음과 같다.

| 작업 | 권장 자원/예산 | 신뢰도 |
|---|---|---|
| baseline 데이터 준비·GSS | 32 core급 CPU, CUDA GPU 1개 이상, 1~2 TB scratch, 1~3일 | 낮음 |
| 제안 구조 1개 full training | CUDA GPU 4~8개, GPU당 24~40 GB 이상, 3~10일 = 약 288~1,920 GPU-hour | 매우 낮음 |
| 논문 ablation 6~8개 | feature cache/초기화 재사용 전 약 2천~1.5만 GPU-hour | 매우 낮음 |
| 제품 hard-selector label 생성 | 60시간×모든 장치 Whisper 실행 + 사람 검수. 현 1시간/2장치의 단일 입력 전사 합계 9,195초를 직렬 환산하면 2장치 약 153시간, 4장치 약 306시간 | 매우 낮음 |
| 제품 hard-selector PoC | label 검수와 frozen feature/SSL embedding 사전 추출 뒤 GPU 1개로 수시간~2일, 3 seed | 중간 이하 |

label 생성 환산은 `single_0` 7,104.225초와 `single_1` 2,091.026초를 더한 단일 샘플 수치로, 병렬화할 수 있고 장치/발화에 따라 크게 달라진다. annotation과 SSL feature 추출 시간은 포함하지 않았다. 첫 full run 전에 데이터 1%와 1 epoch로 throughput·VRAM·I/O를 계측해 예산을 다시 산정한다. CPU만으로 full training하는 계획은 수립하지 않는다.

### 6.4 추론 CPU/GPU 비용

논문 그래프에서 WavLM frontend는 적어도 `K`개의 WPE+BF stream과 1개의 GSS stream을 처리한다. 따라서 feature extraction 계산량은 single-stream WavLM 대비 구조적으로 최소 `K+1` stream-equivalent이며, activation memory는 대체로 `K × T × D`에 비례한다. 여기에 WPE, BeamformIt, GSS, MFCCA, decoder와 LM이 더해진다.

- 2장치: 최소 3개 WavLM 입력 stream
- 4장치: 최소 5개 WavLM 입력 stream
- 논문 최대 fusion 폭: 10채널; 부족한 채널은 반복하므로 fusion 내부 폭이 줄지 않음
- ROVER: 단일 `+ALL` 실행이 아니라 여러 ASR system decode와 투표가 필요

논문이 다룬 장치 수준 조건은 `K=5/6/10`이지만 DiPCo(`K=5`)는 dev/eval 전용이므로 확인 가능한 학습 topology는 `K=6/10`이다. 제품의 2개 독립 장치를 단순 반복해 10채널로 만드는 입력은 보고된 평가 범위 밖이므로, 반복 padding 방식 자체도 한국어 제품 데이터에서 다시 학습·검증해야 한다.

논문에 RTF가 없으므로 CPU wall time을 숫자로 확정하지 않는다. PoC 측정 항목은 front-end/ASR/LM별 wall time, peak RSS/VRAM, CPU 이용률, output audio 1시간당 에너지다. 제품 후보는 현재 V1과 같은 1시간 파일에서 3회 반복하고 median/p95를 남긴다.

### 6.5 NPU 비용과 가능성

현재 개발 장비의 OpenVINO 2026.3 device 목록에는 NPU가 없다. 또한 연구 모델은 PyTorch/ESPnet 그래프이고, 제품에서 NPU로 실행하려면 ONNX/OpenVINO 변환, 정적 shape 정책, quantization과 정확도 회귀 검증이 추가로 필요하다.

[OpenVINO NPU 문서](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html)는 가변 shape 지원을 preview로 설명하며 지원 범위가 제한적이다. 가변 `K`, 가변 utterance 길이, attention/decoder를 가진 이 ASR 전체 그래프가 NPU에서 동작한다고 가정하지 않는다.

| 단계 | 판단 |
|---|---|
| 학습 | NPU 대상 아님. CUDA GPU 사용 |
| CPU inference | 기능 기준선과 fallback. full system은 RTF 측정 전 제품 후보로 보지 않음 |
| NPU inference | 별도 Intel Core Ultra 시험 장비에서 `compile_model` 성공부터 검증 |
| 정적화 전략 | `K=2/4/10`, 고정 10~30초 chunk별 IR을 별도로 만들고 padding/mask 정확도 확인 |
| 정밀도 | FP16 기준 후 INT8 calibration. held-out CER 열화가 기준을 넘으면 중단 |
| 엔지니어링 ROM | 학습 모델 확정 뒤 export/정적화/quantization/benchmark에 1명 4~8주 |

NPU가 일부 연산만 지원할 때 현재 NPU plugin은 임의의 CPU 이종 분할을 전제로 삼기 어렵다. 모델을 frontend/selector/ASR로 명시적으로 나눠 CPU/GPU/NPU별 배치를 설계해야 한다.

## 7. 공정 비교 실험

논문 재현과 제품 결정을 하나의 표로 섞지 않는다.

### 7.1 Track R — 논문 계열 재현

목적은 논문 구조가 CHiME-7 영어 조건에서 동작하는지 확인하는 것이다.

| 조건 | 설명 |
|---|---|
| R-B0 | 공개 CHiME-7 ESPnet baseline checkpoint/recipe, oracle segmentation |
| R-B1 | 독립 재학습한 동일 baseline |
| R-PB | 논문 Table 1 baseline. 저자 config/checkpoint와 Eval scoring 확보 시에만 R3 anchor로 사용 |
| R-M0 | MFCCA |
| R-M1 | MFCCA+CGCS |
| R-M2 | MFCCA+CGCS+GRC |
| R-M3 | 논문 표의 `+FGCS` 조합 재구성(정확한 CGCS 포함 관계는 저자 자료 전까지 미확정) |
| R-M4 | 논문 표의 `+cosIPD` 조합 재구성(CGCS/FGCS 포함 관계 미확정) |
| R-M5 | MFCCA의 기존 convolution fusion을 U-Net형 fusion으로 교체한 논문 표 조건 |
| R-ALL | 모든 제안 요소의 단일 모델 |
| R-ROVER | 부가 결과. 포함 checkpoint와 추가 decode 비용을 명시 |

고정 조건:

- R-B0/R-B1은 pinned 공개 recipe의 GSS·EV top-80% 전처리와 scoring을 유지한다.
- R-PB/R-M*는 논문이 보고한 동일 CHiME split, oracle segmentation, 병렬 WPE/BeamformIt·GSS 산출물을 유지한다.
- 같은 family 안에서 WavLM checkpoint, tokenizer, LM, decoding/scoring을 고정한다. 공개 baseline과 논문 baseline을 서로 같은 checkpoint로 간주하지 않는다.
- 3개 seed와 meeting 단위 confidence interval
- 제안 요소 외 parameter 수 차이, training step, GPU-hour를 함께 보고

Dev baseline을 공개 recipe 값에서 ±1.0%p 안으로 재현하지 못하면 제안 모델 학습에 들어가지 않는다. 저자 구현을 받지 못한 R2 결과에는 “independent reimplementation” label을 붙이고 논문의 22.0/20.0과 직접 동등하다고 쓰지 않는다.

### 7.2 Track P — 현재 제품과의 도입 비교

목적은 한국어 독립 장치 입력에서 현재 V1보다 제품 가치가 있는지 판단하는 것이다.

| 조건 | 입력/처리 | 역할 |
|---|---|---|
| P-S0 | 기준 장치 단독 | 실제 단일 파일 기준선 |
| P-S* | test 정답으로 사후 선택한 best single | 실제로 사용할 수 없는 진단 oracle |
| P-V1 | 현재 align→휴리스틱 score→hard select→Whisper | 제품 기준선 |
| P-OH | 정답을 이용해 탐색하되 V1의 단일채널·hold·switch 제약을 지킨 후보 경로→동일 Whisper | train/dev 전용 oracle-informed headroom gate. 수학적 upper bound가 아니며 test 성공값으로 보고 금지 |
| P-LS | learned score→기존 안정화/override/render→동일 Whisper | 첫 제품 PoC |
| P-END2END | 논문 계열 전체 ASR | 별도 연구 결과, P-LS와 같은 선택기 효과로 해석 금지 |

P-V1과 P-LS에서 반드시 같게 유지할 항목:

- 원본 파일, reference timeline, alignment와 유효 구간
- 주 비교는 제품의 동일 automatic VAD/발화 경계를 사용한다. 같은 oracle segmentation 비교는 VAD 영향을 분리하는 보조 진단으로만 보고 성공 판정에 쓰지 않는다.
- 합성 WAV format, Whisper model/precision/device, beam/prompt/window
- diarization 사용 여부와 모델/threshold
- 실행 장비, warm-up, condition 순서의 무작위화, 3회 반복
- train/dev에서 정한 설정을 blind test 전에 freeze

주 지표:

- 한국어 CER, 보조 WER
- 누락 발화 수와 `overlap`/`far_field`/`reverb` tag별 CER
- 발화별 oracle channel과 자동 선택의 일치율
- 전환 수, 발화 중 전환, low-confidence 비율
- front-end RTF, 전체 RTF, peak RSS/VRAM, 출력 1시간당 처리 시간

통계:

- meeting을 sampling unit으로 한 paired bootstrap 95% CI
- 회의·공간·화자 단위 split으로 누수 방지
- P-LS는 3개 seed를 train/dev에서 비교하고 dev 규칙으로 checkpoint 하나를 blind test 개봉 전에 고정한다. 선택 checkpoint가 primary 판정을 담당하며 나머지 seed의 blind 결과는 판정 후 robustness 평균·범위로만 보고한다.
- 전체 평균뿐 아니라 가장 악화된 회의/발화를 항상 보고

현재 evaluator는 한 회의 안의 발화를 bootstrap하고 후보 이름도 `multi` 하나로 고정한다. 여러 회의 집계와 `multi_v1`/`multi_learned` 대응 채점을 구현하기 전에는 위 confidence interval을 산출했다고 간주하지 않는다. blind test가 독립 회의 10개보다 적으면 meeting bootstrap은 기술적 탐색값으로만 보고 도입 판정에 쓰지 않는다.

ROVER는 primary 비교에서 제외한다. 별도 조건으로 실행할 때는 어떤 시스템을 몇 번 decode했는지와 총 비용을 함께 표시한다.

## 8. PoC 단계와 진입 기준

### 8.1 진입 gate

모든 gate를 한꺼번에 요구하지 않고 트랙과 단계별로 적용한다.

| Gate | 적용 단계 | 통과 조건 | 현재 상태 |
|---|---|---|---|
| G0a 재구현 범위 | R2 | 저자 code/config 확보 또는 independent reimplementation 범위 승인 | **미충족** |
| G0b exact 자료 | R3 | 저자 code/config/checkpoint, 정확한 ROVER 구성과 공식 Eval reference/scoring 접근 확보 | **미충족** |
| G1 법적 접근 | R1/R2/R3 | CHiME-5/6, DiPCo, Mixer6 및 모델 라이선스 검토 완료 | 미확인 |
| G2 재현 데이터 | R1/R2/R3 | 필요한 split, oracle segmentation, 1~2 TB 저장 공간 준비 | 미충족 |
| G3 제품 데이터 | P-LS | 허가된 한국어 50/5/5시간, 최소 회의 수와 metadata 준비 | **미충족** |
| G3a hard headroom | P-LS | dev P-OH가 reference≥30·`M_V1>=10`인 각 tag의 누락과 clean/삽입 목표를 동시에 충족 | 미실행 |
| G4 연구 연산 | R1/R2/R3 | R1 실행 GPU 1개; R2/R3는 CUDA GPU 4개 이상 또는 축소 계획과 예산 소유자 지정 | 미확정 |
| G5a 경로 smoke | R1 | 공개 checkpoint로 전처리→decode→공식 score가 완주하고 산출물 provenance가 남음 | 미실행 |
| G5b 기준선 parity | R2/R3 | pinned 공개 recipe의 Dev Macro 31.8을 동일 조건에서 ±1.0%p 이내 재현 | 미실행 |
| G5c 논문 기준선 | R3 | 저자 baseline 조건으로 Dev 28.8/Eval 33.4를 공식 scoring에서 각각 ±1.0%p 이내 재현 | Eval 접근 부재로 차단 |
| G6a 연구 통합 | P-LS 학습 전 | scorer injection, versioned feature/model metadata, PoC CLI와 learned-path timing hook을 구현하고 단위 test 통과 | 미구현 |
| G6b 평가 동결 | P-LS blind 전 | 다회의 paired scorer, `multi_v1`/`multi_learned` artifact 매핑, tag별 누락·삽입과 비용 계측을 구현·검증하고 config/hash 동결 | 미구현 |
| G6c 제품 통합 | 제품 merge 전 | GUI adapter, packaging/model lifecycle, progress/cancel, 오류·fallback 경로를 구현하고 Qt/backend 회귀 test 통과 | 미구현 |
| G7 기준 장비 | P-LS | CPU/GPU 최소 지원 장비, driver/OpenVINO version, RAM/VRAM 예산 고정 | 미확정 |
| G8 NPU 장비 | NPU 단계 | 실제 NPU 장비·driver/OpenVINO version과 export 대상 checkpoint 고정 | **미충족** |

- R1은 G1·G2와 G4의 R1 자원으로 시작한다. G5a는 실행 경로만 검증하고 G5b는 수치 parity를 별도로 검증한다.
- P-LS 학습은 G3·G3a·G6a·G7이 필요하고 G0·CHiME gate에는 종속되지 않는다. blind 개봉 전에는 G6b, 제품 merge 전에는 G6c까지 통과해야 한다.
- R2는 G0a·G1·G2·G4·G5b 통과 뒤에만 시작한다. G5a만으로 R2를 해제하지 않는다.
- R3는 G0b·G1·G2·G4·G5b·G5c가 모두 필요하며 independent reimplementation 승인으로 대체할 수 없다.
- NPU 단계는 P-LS 또는 R2가 정확도·CPU/GPU 비용 기준을 먼저 통과하고 G8이 준비된 뒤 시작한다.

### 8.2 권장 순서

1. **자료 확보(1주):** 저자에게 코드/config/checkpoint·ROVER 구성·hardware 정보를 문의한다.
2. **baseline smoke/parity(1~2주):** CHiME-7 일부와 공개 ESPnet checkpoint로 G5a를 확인한 뒤 전체 Dev 동일 조건으로 G5b를 별도 판정한다.
3. **제품 데이터 준비(병행):** MM-DEV-017 형식의 사람 정답과 device metadata를 만들고 모든 장치의 oracle label을 생성·검수한다.
4. **P-OH headroom(1~2주):** oracle-informed 후보 경로를 렌더링·전사해 단일 hard selector가 성공 기준에 도달할 경험적 여지가 있는지 먼저 판정한다.
5. **P-LS 연구 prototype(2~4주):** P-OH 통과 뒤 G6a 연구 scaffold를 먼저 구현하고, 그 경계에서 현재 품질 특징 ranker를 학습한다.
6. **표현 확장(2~4주):** frozen SSL embedding을 추가하고 P-LS와 ablation 비교한다.
7. **제품화(선택, 추가 2~4주):** 선택 모델의 packaging, lifecycle, progress/cancel, 오류 처리와 Qt/backend 회귀 test를 완료한다.
8. **논문 구조 R2(선택, 6~12주):** 앞 단계에 신호가 있고 연구 예산이 있을 때만 CGCS/FGCS/MFCCA를 독립 재구현한다.
9. **OpenVINO/NPU(선택, 4~8주):** 정확도와 GPU/CPU 비용 gate를 먼저 통과한 단일 모델만 변환한다.

기간은 데이터 확보·annotation·oracle label 생성 시간을 제외한 1명 기준 ROM이다. P-LS 2~4주는 연구 prototype이며 제품 lifecycle과 회귀 검증은 7단계의 별도 ROM이다. label 생성만도 현재 단일 샘플 직렬 환산으로 2장치 약 153시간, 4장치 약 306시간이므로 별도 일정과 병렬 자원이 필요하다.

### 8.3 성공 기준

제품 P-LS의 blind test 성공 기준은 MM-DEV-018의 누락·clean·전체 시간 기준 일부를 계승하고, 선택기 비용·삽입·전환 기준을 새로 추가한다. 정확도 차이는 blind 개봉 전에 선택한 한 checkpoint에 대해 `P-LS - P-V1`의 paired meeting bootstrap으로 계산한다.

- 전체 CER 차이의 점 추정치가 `-1.0%p` 이하이고 95% CI 상한이 `0` 미만
- `overlap`과 `far_field` 각각에서 pooled 누락 수의 상대 감소 `(M_V1 - M_LS) / M_V1` 점 추정치가 30% 이상이고, meeting bootstrap 95% CI 하한이 0보다 큼
- 겹침 없는 발화 CER 차이의 95% CI 상한이 `+1.0%p` 이하
- 삽입률 차이의 95% CI 상한이 `+1.0%p` 이하
- 동일 1시간 입력 3회에서 전체 처리 시간 중앙값이 P-V1의 1.5배 이하
- 같은 실행에서 P-LS front-end 중앙값이 P-V1의 2배 이하, learned path warm incremental RTF가 0.015 이하(오디오 1시간당 54초), cold model load가 5초 이하. learned path 시간에는 feature 추출, cache miss/file I/O, tensor 변환, model inference와 report adapter를 모두 포함하고 사전 계산 feature는 허용하지 않는다.
- 기준 장비에서 1시간 입력을 OOM 없이 완료하고 peak 사용량 기준 RAM/VRAM 용량이 각각 25% 이상 남음. peak RSS와 VRAM은 섞지 않고 따로 기록
- 총 channel switch가 `min(P-V1, 10회/분)` 이하이고, 발화 중 전환 수가 P-V1보다 늘지 않음. 현재 한 샘플 P-V1은 566회/3,627.65초, 9.36회/분이나 사람 정답이 없어 참고값일 뿐이다.
- 오류/취소 시 기존 원본·결과 게시 계약 유지

tag별 전체 blind test에서 `M_V1 < 10`이면 30% 상대 감소의 분모가 불안정하므로 그 tag의 gate는 통과가 아니라 **판정 불가**로 둔다. `M_V1=0`이면 상대 감소 자체도 정의하지 않는다. bootstrap resample에서만 분모가 0인 표본은 제외하며, 이런 표본이 5%를 넘으면 CI도 판정 불가로 처리한다. seed 선택 규칙이나 이 계산법은 blind 결과를 본 뒤 바꾸지 않는다.

논문 계열 R2의 성공은 별도로 판정한다.

- R-B0 재현 gate 통과
- 같은 seed/step/LM 조건에서 R-ALL이 자체 baseline보다 유의하게 개선
- 성능표에 ROVER 전/후와 비용을 분리
- 공개 가능한 config, commit, environment, input manifest, checkpoint SHA-256 보존

### 8.4 중단 조건

다음 중 하나면 full FGCS/CGCS 또는 NPU 단계로 확대하지 않는다.

- 허가된 학습 데이터나 Mixer6/CHiME 라이선스를 확보할 수 없음
- phase 안정성 검사에서 독립 장치 cosIPD가 meeting/장치별로 재현되지 않음
- P-LS가 blind test에서 V1보다 개선되지 않음
- clean/non-overlap CER, 삽입률, 전환 수가 위 성공 기준을 초과해 악화
- selector 또는 CPU/GPU 전체 시간이 위 기준을 넘고 선택적 실행으로 줄일 수 없음
- 정적 OpenVINO IR compile 실패 또는 같은 checkpoint의 FP32 대비 FP16/INT8 CER 차이 95% CI 상한이 `+1.0%p` 초과
- 저자 자료 없이 독립 구현의 불확실성을 감수할 연구 목적/예산이 없음

## 9. 해석 제한

- 논문 수치는 영어 CHiME-7, oracle segmentation, WPE/BeamformIt/GSS와 전용 ASR 조건이다. 한국어, automatic VAD, 소비자 장치 2~4대에서의 개선 근거가 아니다.
- 논문이 다룬 세 topology 결과를 임의 topology 보장으로 해석하지 않는다. 특히 제품의 `K=2`와 손실 압축 입력은 보고 범위 밖이다.
- 제안 FGCS/CGCS 모델을 이번 작업에서 실행하지 않았다. 공식 구현이 공개 검색에서 확인되지 않아 구조 재구현 결과가 다를 때 설정 차이와 구현 오류를 분리하기 어렵다.
- 학습 시간·GPU-hour·저장 공간·1명 기준 일정은 공개된 실측이 아니라 ROM이다. 데이터 1%와 1 epoch 측정 뒤 갱신해야 한다.
- 로컬 MM-DEV-017 샘플에는 검증된 사람 정답과 학습·평가 사용 동의 기록이 없다. 이 문서에서는 런타임 기준선에만 사용했고 정확도·학습 근거로 쓰지 않았다.
- 제품 장치의 phase/IPD 안정성을 측정하지 않았다. cosIPD 보류는 실패 실측이 아니라 현재 보정 경로의 동등성이 입증되지 않았다는 위험 판단이다.
- P-LS는 논문에서 영감을 받은 제품용 proxy이며 FGCS/CGCS 재현 결과로 보고할 수 없다.
- 50/5/5시간, 최소 회의 수와 성공 임계값은 사전 등록용 설계 제안이다. pilot의 분산과 effect size로 power를 다시 계산하되 blind test를 본 뒤 임계값을 완화하지 않는다.
- 이번 티켓에서는 조사·설계 문서만 작성했다. 모델 prototype, 제품 runtime dependency, Python backend, Qt/GUI와 자동화 test는 변경하거나 실행하지 않았다.

## 10. 최종 권고

1. **V1은 변경하지 않는다.** 현재 휴리스틱은 빠르고, 설명 가능하며, 기존 Whisper/override/진단 계약을 유지한다.
2. **MM-DEV-019의 full FGCS/CGCS 구현은 gate 대기 상태로 둔다.** 현재는 exact reproduction과 제품 배포 근거가 부족하다.
3. **후속 구현이 필요하면 P-OH headroom probe부터 시작한다.** 이 보수적 gate를 통과한 경우에만 현재의 window 특징과 출력 계약을 유지하는 P-LS를 가장 작은 학습 PoC로 진행한다.
4. **cosIPD는 기본 범위에서 제외한다.** 독립 장치의 위상 안정성이 실측될 때만 ablation 후보로 넣는다.
5. **40.1%를 목표치로 사용하지 않는다.** 제품 판정은 동일 Whisper의 P-V1/P-LS blind Korean test로 하고, ROVER 결과는 비용을 포함한 부가 연구 결과로만 보고한다.
6. **프로토타입 코드는 이번 단계에서 만들지 않는다.** P-LS는 G3/G3a/G6a/G7, R2는 G0a/G1/G2/G4/G5b가 막혀 있다. gate 없이 임의 구현하면 재현 가능한 도입 근거를 만들지 못하고 제품 의존성만 늘린다.

## 11. 확인한 공개 자료

- [Mu et al., arXiv:2312.09746 HTML](https://arxiv.org/html/2312.09746v1): 모델 구조, 276시간, oracle segmentation, ablation/ROVER 결과
- [Mu et al., CHiME 2023 system paper](https://www.isca-archive.org/chime_2023/mu23_chime.html): 최종 논문의 선행 시스템과 구조 version 차이 확인
- [CHiME-7 DASR task paper](https://www.isca-archive.org/chime_2023/cornell23_chime.pdf): baseline, acoustic robustness sub-track와 oracle segmentation
- [CHiME-7 task 정의](https://www.chimechallenge.org/challenges/chime7/task1/index), [data](https://www.chimechallenge.org/challenges/chime7/task1/data), [submission](https://www.chimechallenge.org/challenges/chime7/task1/submission): Macro DA-WER, corpus split·장치 동기 조건, 종료된 Eval 채점 접근
- [ESPnet CHiME-7 recipe, commit `89ebca4`](https://github.com/espnet/espnet/tree/89ebca463c544dfaa19e5f76ad5f615f473f6957/egs2/chime7_task1): 공개 baseline 구현 출발점
- [공개 CHiME-7 baseline checkpoint, revision `9f28e8a`](https://huggingface.co/popcornell/chime7_task1_asr1_baseline/tree/9f28e8a021e54e9d4d667c14e0255ea90cb6d701): WavLM/Transformer config와 약 1.38 GB artifact
- [Fiscus, ROVER 원 논문(NIST)](https://www.nist.gov/publications/post-processing-system-yield-reduced-word-error-rates-recognizer-output-voting-error): ASR hypothesis 정렬·투표 후처리
- [Microsoft WavLM](https://github.com/microsoft/unilm/tree/master/wavlm): WavLM Large 구조와 checkpoint
- [OpenVINO 2026 NPU device 문서](https://docs.openvino.ai/2026/openvino-workflow/running-inference/inference-devices-and-modes/npu-device.html): NPU platform·shape 제약
- [CHiME-6 data/license](https://chimechallenge.github.io/chime6/download), [Mixer 6 LDC catalog](https://catalog.ldc.upenn.edu/LDC2013S03): 데이터 접근 조건

