# Vehicle Scheduling System

固定軌道網絡上的車輛調度後端服務，純 REST API。功能涵蓋車輛 / 服務 CRUD、路徑驗證、衝突偵測（區塊 / 互鎖 / 電池 / 同車多服務）、以及自動排班生成。

詳細設計請見 [DESIGN.md](DESIGN.md)。

## 技術棧

- Python 3.13 + FastAPI
- PostgreSQL 16（async，透過 SQLAlchemy 2.x async + asyncpg）
- Alembic（schema migration）
- uv（相依管理）+ ruff（lint / format）
- Docker Compose（db + api）

## 一鍵啟動

```bash
cp .env.example .env   # 依需要改 POSTGRES_PASSWORD
docker compose up --build
```

啟動流程：

1. Postgres 16 起來並通過 `pg_isready` healthcheck。
2. API 容器啟動前執行 `alembic upgrade head`，套用 schema migrations。
3. 應用啟動時以 upsert 寫入 `BlockConfig` 預設值（每個區塊 60 秒）。
4. Uvicorn 服務監聽 `:8000`。

- OpenAPI 文件：<http://localhost:8000/docs>
- Healthcheck：<http://localhost:8000/health>

## 本地開發

```bash
uv sync
uv run uvicorn app.main:app --reload
uv run pytest
uv run ruff check
```

測試會透過 testcontainers 自動啟動一個 `postgres:16-alpine` container（session scope），跑 `alembic upgrade head`，每個 test 之間以 `TRUNCATE ... RESTART IDENTITY CASCADE` 重置狀態。前置條件：本機要有 Docker daemon。

## API 概覽

| Method | Path | 說明 |
|--------|------|------|
| `GET`    | `/api/v1/vehicles`              | 列出車輛 |
| `POST`   | `/api/v1/vehicles`              | 建立車輛 |
| `GET`    | `/api/v1/vehicles/{id}`         | 查詢車輛 |
| `PATCH`  | `/api/v1/vehicles/{id}`         | 更新車輛（部分欄位） |
| `DELETE` | `/api/v1/vehicles/{id}`         | 刪除車輛（有關聯行程時回 409） |
| `GET`    | `/api/v1/blocks`                | 列出區塊通行時間 |
| `PUT`    | `/api/v1/blocks/{block_id}`     | 更新區塊通行時間 |
| `GET`    | `/api/v1/services`              | 列出所有行程 |
| `POST`   | `/api/v1/services`              | 建立行程（路徑驗證 + 同車衝突擋檢） |
| `GET`    | `/api/v1/services/{id}`         | 查詢行程 |
| `PUT`    | `/api/v1/services/{id}`         | 更新行程 |
| `DELETE` | `/api/v1/services/{id}`         | 刪除行程 |
| `GET`    | `/api/v1/schedule`              | 依出發時間排序的排班視圖 |
| `GET`    | `/api/v1/schedule/conflicts`    | 偵測所有衝突 |
| `POST`   | `/api/v1/schedule/generate`     | 自動生成無衝突排班（Bonus 3） |
| `GET`    | `/api/v1/topology`              | 拓撲圖 + 區塊通行時間 + 電池常數（Bonus 2 後端）|
| `GET`    | `/api/v1/topology/positions`    | 指定時刻所有車輛位置 + 電量（Bonus 2 後端）|
| `POST`   | `/graphql`                      | 唯讀 GraphQL gateway（details 見 [DESIGN.md §9.13](DESIGN.md#913-bundle-d唯讀-graphql-gateway)）|

## 資料模型重點

- `vehicles`：`id`、`name`（unique）。電量不再是欄位，改由 `battery_events` ledger 投影（見下）
- `services`：`id`、`vehicle_id`、`departure_battery`（write-time cache，來自 ledger）、`created_at`、`updated_at`
- `service_stops`：每個節點一筆（含 block / platform / yard），platform 與 yard 存 `arrival_time` / `departure_time`；block 的通行時間由 `block_configs.traversal_seconds` 計算得出，確保為單一事實來源
- `battery_events`：append-only 電量事件帳本，event_type ∈ {`BASELINE`, `SERVICE_CONSUME`, `YARD_CHARGE`, `MANUAL_ADJUST`}；current battery = `sum(delta)`；service 刪除時相關 consume event FK cascade 刪除
- `block_configs`：`block_id` PK、`traversal_seconds`

完整 ER 與設計決策說明在 [DESIGN.md §5](DESIGN.md#5-資料模型設計)。

## 設計權衡摘要

- **拓撲硬編碼 + 版本化刻意延後**：軌道圖在作業範圍內固定，以 `Final` 常數存於 [app/topology.py](app/topology.py)，避免每次呼叫查 DB。拓撲 DB 化 + `topology_version` 綁定到 service 的方案（容許管理端改圖而不破壞歷史排班）已有計劃，見 [DESIGN.md §10.4.5](DESIGN.md#1045-拓撲動態化管理介面可改-block--adjacency)；本次作業未實作，原因：觸發條件（使用者自行改圖）不在需求範圍，blast radius 高（每個 topology 常數存取點都要改），投入 / 產出比不划算。
- **衝突偵測不阻擋寫入（單純 GET 偵測）；建立 / 更新 service 時才擋同車重疊**：手動排班允許暫時性衝突以便調整，但同車多服務時間重疊是 impossible state，所以在寫入端點 fail fast。
- **自動排班用 greedy**：易理解且可保證無衝突，缺點是不會回溯。多台車輛會以 `interval / N` 錯開出發，保證乘客等待時間 ≤ 班距。
- **Topology / Positions 後端 API 做、前端不做**：Bonus 2 題目包含互動地圖。我們只提供資料 API，把視覺化留給客戶端。下面幾個子權衡：
  - **`GET /topology` 一次吐完整圖（含常數）**：優點：模擬 / 渲染端一次拿齊、不用拼多個 endpoint；`block_configs` 更新後也會一併反映在 `traversal_seconds`。缺點：payload 略大、若未來加入大量節點 metadata 需再拆。在目前 21 節點規模不是問題。
  - **`GET /topology/positions?at=<iso>` 單點查詢、強制 `at` 必填**：優點：純函數、易快取、測試容易；不預設 `now()` 避免隱性時間狀態。缺點：回放需客戶端自行輪詢，沒有批次 `?start=&end=&step=`。在 1Hz 輪詢 + 少量車輛情境夠用，真有瓶頸再加批次端點。
  - **Position 電量用線性內插 vs. conflict_detector 用 step function**：前者讓前端繪製平滑下降曲線（每一幀位置都不同）；後者保證「過一個 block 扣一單位」的整數不變量，對衝突偵測語意乾淨。兩者刻意**不統一**：顯示用連續值、判斷用離散值，誰誤差都小於 1 單位，不會造成 API 表現矛盾。已在 [app/logic/positions.py](app/logic/positions.py) docstring 註明。
  - **Idle 時 `current_node` 取上一個服務終點**：避免前端看到車輛從畫面消失；缺點：如果使用者相信那台車真的在 P1A 月台（其實已經沒在服務中），可能誤判。靠 `status="idle"` 讓客戶端以灰色 / 虛線呈現以區分。
  - **不提供 WebSocket / SSE**：保持純 REST。若要真即時推送，需要另一套連線管理與授權層，超過作業範圍。
- **Day-1 衛生升級（Bundle A）**（詳見 [DESIGN.md §9.11](DESIGN.md#911-bundle-aapi--演算法衛生升級)）：
  - `detect_block_conflicts` 先按 `enter` 排序 + early exit，同一對 service × 多 block 衝突聚合為單筆 `Conflict`（新增 `locations: list[str]`）
  - `ServiceStopCreate` / `GenerateRequest` 改用 Pydantic v2 `AwareDatetime`，naive datetime 在 schema 邊界即擋
  - `/topology/positions` 新增 `mode=simulation|strict`：前者線性內插（UI 平滑）、後者 step function 與 conflict detector 對齊（審計用）
  - 錯誤回應統一為 `{"error": {"code", "message", "fields|errors|conflicts"}}` envelope（`app/errors.py`），client 一套 parser 可解
- **電量事件化（Bundle B）**（詳見 [DESIGN.md §9.12](DESIGN.md#912-bundle-b電量事件化event-sourced-battery)）：`vehicles.battery_level` 欄位 drop，改為 `battery_events` append-only ledger + `services.departure_battery` write-time cache。每筆 service 在 end time 發 `SERVICE_CONSUME`、PATCH battery 發 `MANUAL_ADJUST`、刪 service 由 FK cascade 清 event。好處：每筆歷史 service 都拿得到當下正確的 departure battery（先前所有 service 共用「現在的電量」是錯的）；壞處：read path 多一次 aggregate（有 cache 緩解）。

## 前提假設

- 所有時間皆為 timezone-aware（UTC）。
- 一台車同一時刻只能執行一個行程。
- 行程結束位置需連續到下一個行程的起點（否則視為位置不連續衝突）。
- 自動排班使用固定往返路徑（Y → P1A → P2A → P3A → P2B → P1A → Y，經 10 個 block），未來可擴充動態選路。
