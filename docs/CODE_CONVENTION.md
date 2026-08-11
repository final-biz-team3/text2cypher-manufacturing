# Python 코드 컨벤션 및 품질 관리 가이드

## 1. 코드 컨벤션이란?

> 여러 개발자가 일관된 형태로 코드를 작성할 수 있도록 정한 규칙과 작성 기준이다.

코드 컨벤션에는 변수와 함수의 이름을 짓는 방법, 들여쓰기, 공백, Import 순서,
주석 및 예외 처리 방식 등이 포함된다. 코드의 실행 결과를 바꾸기 위한 규칙이라기보다,
코드를 쉽게 읽고 이해하며 유지보수할 수 있도록 작성 방식을 통일하는 데 목적이 있다.

### 1.1 코드 컨벤션이 필요한 이유

- 팀원마다 다른 코드 작성 방식을 통일할 수 있다.
- 코드의 가독성과 이해도를 높일 수 있다.
- 코드 리뷰와 유지보수가 쉬워진다.
- 스타일 차이로 발생하는 불필요한 수정을 줄일 수 있다.
- 자동화 도구를 이용하여 일정한 코드 품질을 유지할 수 있다.

## 2. Python 코드 컨벤션

- 본 프로젝트는 Python 공식 코드 스타일 가이드인 **PEP 8**을 기본 코드 컨벤션으로 사용한다.

### 2.1 PEP 8이란?

PEP 8은 Python 코드의 가독성과 일관성을 높이기 위해 작성된 공식 코드 스타일 가이드이다.
들여쓰기, 공백, Import, 이름 작성 방식, 주석 등 Python 코드를 작성할 때 권장되는 기준을 설명한다.

> PEP 8의 모든 내용이 반드시 지켜야 하는 문법은 아니다. 프로젝트의 특성과 팀의 합의에 따라
> 별도의 규칙을 추가하거나 일부 기준을 조정할 수 있다.

### 2.2 프로젝트 적용 기준

> 본 프로젝트는 PEP 8을 기본 코드 스타일로 사용한다. 다만 PEP 8에서 정하지 않거나 여러 방식을
> 허용하는 항목은 프로젝트의 협업 환경에 맞게 별도의 규칙으로 정의한다.

- **문자열은 큰따옴표를 사용한다.**
  - PEP 8은 문자열에 사용할 따옴표를 강제하지 않는다. 본 프로젝트에서는 코드의 일관성을 위해
    큰따옴표(`"`)를 기본으로 사용한다.
  - 문자열 안에 큰따옴표가 포함되어 이스케이프가 많아지는 경우에는 작은따옴표를 사용할 수 있다.
- **코드 식별자는 영어로 작성한다.**
  - 변수명, 함수명, 클래스명 등 코드에서 사용하는 이름은 영어로 작성한다. 한글 식별자나 한국어
    발음을 영문으로 옮긴 이름은 사용하지 않는다.
- **주석과 Docstring은 한국어로 작성한다.**
  - 함수명, 매개변수명 및 클래스명과 같은 코드 식별자는 영어를 유지한다.
- **주석에는 코드의 이유와 의도를 작성한다.**
  - 코드만 보고 알 수 있는 동작을 반복하지 않는다. 코드에서 확인하기 어려운 업무 규칙, 예외 처리
    이유 또는 구현 의도를 작성한다.
- **도메인 용어를 통일한다.**
  - 같은 업무 개념에는 동일한 영문 용어를 사용한다. 필요한 경우 별도의 용어 사전을 작성한다.
- **사용자 메시지와 시스템 로그의 언어를 구분한다.**
  - 사용자에게 노출되는 안내와 오류 메시지는 한국어로 작성한다.
  - 시스템 로그는 검색과 외부 모니터링 도구의 활용을 고려하여 영어를 기본으로 작성한다.

### 2.3 프로젝트 규칙 관리 방법

> 프로젝트 규칙은 문서로만 작성하지 않고, 규칙의 성격에 맞는 파일을 저장소에 추가하여 관리한다.

- 사람이 판단해야 하는 규칙은 `docs/CODE_CONVENTION.md`에 기록한다.
- Black, Ruff, mypy의 공통 기준은 루트 `pyproject.toml`에서 관리한다.
- IDE의 인코딩, 들여쓰기와 줄바꿈 규칙은 루트 `.editorconfig`에서 관리한다.
- 커밋 전 검사는 루트 `.pre-commit-config.yaml`에서 관리한다.
- Push와 Pull Request 검사는 `.github/workflows/code-quality.yml`에서 관리한다.

## 3. 코드 품질 관리 도구

> 코드 컨벤션을 문서로만 관리하면 개발자가 규칙을 빠뜨리거나 코드 리뷰에서 스타일 관련 검토가
> 반복될 수 있다. 본 프로젝트는 Black, Ruff, mypy를 사용하여 코드 형식, 잠재적인 오류 및 타입
> 사용을 자동으로 검사한다.

### 3.1 Black

> Black은 Python 코드를 일정한 형식으로 자동 정리하는 코드 포맷터이다. 개발자가 직접 공백,
> 줄바꿈, 들여쓰기와 따옴표 형식을 수정하지 않아도 Black이 프로젝트의 설정에 맞게 코드를 변경한다.

- Black은 코드의 형식을 변경하지만 사용하지 않는 변수, 잘못된 Import 또는 타입 오류를 검사하지 않는다.

```bash
# 코드를 자동으로 정리
black backend

# 수정하지 않고 포맷 적용 여부만 검사
black --check backend
```

### 3.2 Ruff

> Ruff는 Python 코드의 스타일 위반과 잠재적인 오류를 검사하는 Linter이다. Black이 코드의 모양을
> 정리한다면, Ruff는 코드 안에 불필요하거나 잘못 작성된 부분이 있는지 확인한다.

- 모든 Ruff 오류가 자동으로 수정되는 것은 아니다. 코드의 의미가 달라질 가능성이 있는 문제는
  개발자가 직접 확인하여 수정해야 한다.
- 검사 규칙은 `pyproject.toml`의 `[tool.ruff]`와 `[tool.ruff.lint]`에서 관리한다.
- 사용하지 않는 Import와 변수, 정의되지 않은 이름, Import 순서, 이름 규칙, 위험한 코드 패턴 및
  오래된 Python 문법 등을 검사한다.

```bash
# 문제를 검사
ruff check backend

# 안전하게 수정할 수 있는 문제를 자동으로 수정
ruff check backend --fix
```

### 3.3 mypy

> mypy는 Python의 타입 힌트를 기반으로 타입 사용 오류를 검사하는 정적 타입 검사 도구이다.
> 프로그램을 직접 실행하지 않고 함수가 요구하는 타입과 실제로 전달된 타입이 일치하는지 확인한다.

- mypy는 타입 오류를 알려주지만 코드를 자동으로 수정하지 않는다.
- 함수 매개변수와 반환값, 변수에 할당된 값, `None` 처리, 속성 및 메서드 사용 등을 검사한다.

```bash
mypy backend
```

| 구분 | Black | Ruff | mypy |
| --- | --- | --- | --- |
| 주요 역할 | 코드 포맷팅 | 코드 스타일 및 오류 검사 | 정적 타입 검사 |
| 검사 대상 | 공백, 줄바꿈, 들여쓰기, 따옴표 | Import, 이름, 코드 패턴, 잠재적 오류 | 매개변수, 반환값, 변수의 타입 |
| 코드 자동 수정 | 자동 수정 | 일부 항목 자동 수정 | 수정하지 않음 |
| 코드 실행 필요 | 필요 없음 | 필요 없음 | 필요 없음 |
| 기본 명령어 | `black backend` | `ruff check backend` | `mypy backend` |
| 설정 위치 | `[tool.black]` | `[tool.ruff]` | `[tool.mypy]` |

## 4. 개발 환경 및 pre-commit 설정

> `text2cypher-manufacturing`은 `backend/venv`와 `backend/requirements.txt`를 사용하는 모노레포이다.
> 개발용 품질 도구는 `backend/requirements-dev.txt`로 분리하고, 공통 설정은 저장소 루트에서
> 관리한다. pre-commit은 커밋 전에 백엔드 Python 파일만 검사한다.

### 4.1 개발용 가상환경 구성

프로젝트는 Python 3.11을 사용한다. 루트의 `.python-version`에도 같은 버전을 기록하며,
가상환경을 생성할 때 버전을 명시한다.

```bash
# macOS 또는 Linux
python3.11 -m venv backend/venv

# Windows
py -3.11 -m venv backend/venv
```

macOS 또는 Linux에서는 다음 명령으로 가상환경을 활성화한다.

```bash
source backend/venv/bin/activate
```

Windows PowerShell에서는 다음 명령을 사용한다.

```powershell
backend\venv\Scripts\Activate.ps1
```

개발 및 품질 검사에 필요한 패키지를 함께 설치한다.

```bash
python -m pip install -r backend/requirements-dev.txt
```

`backend/requirements-dev.txt`는 기존 `requirements.txt`를 포함한 뒤 Black, Ruff, mypy와
pre-commit의 검증된 버전을 추가한다. 따라서 개발자는 파일 하나만 설치하면 실행 의존성과 개발
의존성을 모두 준비할 수 있다.

### 4.2 프로젝트 공통 설정

- `pyproject.toml`은 Black, Ruff, mypy의 검사 기준을 저장한다.
- `.editorconfig`는 UTF-8, LF 줄바꿈과 Python Space 4칸 들여쓰기를 지정한다.
- `.vscode/extensions.json`은 Python, Pylance, Black, Ruff, mypy 확장 프로그램을 권장한다.
- `.vscode/settings.json`은 자동 Import 제안, 저장 시 Black 포맷, Ruff 안전 수정과 mypy 진단을
  활성화한다.

VS Code 설정은 Black, Ruff와 mypy가 가상환경에 설치된 버전을 사용하도록 구성한다. 팀원은 VS Code의
`Python: Select Interpreter`에서 `backend/venv`의 Python 실행 파일을 선택해야 한다.

### 4.3 pre-commit 설정

루트 `.pre-commit-config.yaml`은 다음 순서로 백엔드 코드를 검사한다.

```text
Ruff 안전 수정 → Black 포맷팅 → mypy 타입 검사
```

Ruff의 수정 결과를 Black이 최종 정리하도록 Ruff를 먼저 실행한다. mypy Hook에는 백엔드 코드를
분석하는 데 필요한 FastAPI, Neo4j와 pydantic-settings를 추가하여 pre-commit의 독립 환경에서도
동일한 타입 검사를 실행하도록 한다.

설정 파일을 받은 뒤 Git Hook을 한 번 설치한다.

```bash
pre-commit install
```

처음 적용하거나 설정을 변경했을 때는 추적 중인 전체 파일을 검사한다.

```bash
pre-commit run --all-files
```

이후 `git commit`을 실행하면 Hook이 자동으로 실행된다. Black이나 Ruff가 파일을 수정하면 변경 내용을
확인하여 다시 스테이징한 후 커밋한다. 검사를 건너뛸 수 있는 로컬 Hook만으로는 최종 품질을 보장할 수
없으므로 같은 검사를 CI에서도 실행한다.

### 4.4 저장소에 추가되는 파일

```text
text2cypher-manufacturing/
├── .editorconfig
├── .pre-commit-config.yaml
├── pyproject.toml
├── .vscode/
│   ├── extensions.json
│   └── settings.json
└── backend/
    ├── requirements.txt
    └── requirements-dev.txt
```

위 파일은 개인 설정이 아니라 팀 전체가 사용하는 기준이므로 Git에 커밋한다. `backend/venv`와 각 도구가
생성하는 캐시 파일은 로컬에서만 사용하고 `.gitignore`로 제외한다.

## 5. 프로젝트 CI 기반 코드 품질 검사

> 로컬 pre-commit을 실행하지 않았더라도 원격 저장소에서 같은 코드 품질 기준을 확인하기 위해
> GitHub Actions를 사용한다.

GitHub Actions 규칙에 따라 루트의 `.github/workflows/code-quality.yml`에 CI 설정을 저장한다.
`backend`, `pyproject.toml` 또는 Workflow 자체가 변경된 `dev` 또는 `main` 대상 Push와 Pull Request에서만
Python 3.11과 개발 의존성을 준비한 뒤 다음 검사를 실행한다.

```text
Ruff 검사 → Black 포맷 검사 → mypy 타입 검사
```

자동 수정은 VS Code와 pre-commit에서 처리한다. CI는 저장소 쓰기 권한 없이 검사만 수행하며, 하나라도
실패하면 개발자가 로컬에서 문제를 수정한 뒤 다시 Push한다.

```yaml
name: Backend Code Quality

on:
  push:
    branches: [dev, main]
    paths:
      - "backend/**"
      - "pyproject.toml"
      - ".github/workflows/code-quality.yml"
  pull_request:
    branches: [dev, main]
    paths:
      - "backend/**"
      - "pyproject.toml"
      - ".github/workflows/code-quality.yml"

permissions:
  contents: read

jobs:
  code-quality:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v6

      - uses: actions/setup-python@v6
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: backend/requirements-dev.txt

      - name: Install dependencies
        run: python -m pip install -r backend/requirements-dev.txt

      - name: Run code quality checks
        run: |
          ruff check backend
          black --check backend
          mypy backend
```

## 참고 자료

- [PEP 8](https://peps.python.org/pep-0008/)
- [Black 공식 문서](https://black.readthedocs.io/en/stable/)
- [Ruff 공식 문서](https://docs.astral.sh/ruff/)
- [mypy 공식 문서](https://mypy.readthedocs.io/en/stable/)
- [pre-commit 공식 문서](https://pre-commit.com/)
- [EditorConfig 공식 문서](https://editorconfig.org/)
