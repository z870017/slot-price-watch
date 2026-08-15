# スロット實機 跨站比價（Phase 0）

抓取三家日本中古パチスロ／パチンコ實機店的商品與價格，自動辨識「哪幾筆其實是同一台機」，
並算出跨站價差。

| 站 | 平台 | 說明 |
|---|---|---|
| [ホームスロット](https://home-slot.net/) | ShopServe 系 | 約 50 個廠商分類 |
| [A-SLOT](https://www.a-slot.com/) | ShopServe 系 | 60+ 廠商分類 |
| [イニシャルP](https://initialp.cart.fc2.com/) | FC2 カート | 對機器人較敏感，抓取節奏最慢 |

**執行成本：新台幣 0 元。** 排程跑在 GitHub Actions 免費額度，頁面掛在 GitHub Pages，
資料庫是一個存在 repo 裡的 SQLite 檔。沒有伺服器要養。

---

## Phase 0 想回答的問題

整個專案值不值得做下去，只取決於一個數字：**三站到底有幾台機種是重疊的、價差多大。**

跑完一輪後，`Phase0 結論` 那張工作表會直接給出答案：

```
抓取商品         2,431 件
辨識機種         1,876 台
可跨站比價         214 台   ← 就是這個數字
價差中位數       ¥18,500   ← 和這個
最大價差         ¥98,000
```

如果「可跨站比價」只有個位數，那就不必做 Phase 1 了，客戶自己開三個分頁還比較快。
拿這個數字去談後續，比拿想像去談有說服力得多。

---

## 部署（約 10 分鐘）

### 1. 建 repo

把這個資料夾推上去一個新的 GitHub repo（**public 或 private 都可以**；
public repo 的 Actions 免費額度是無限，private 每月 2000 分鐘，也夠用）。

```bash
git init && git add . && git commit -m "init"
git branch -M main
git remote add origin https://github.com/<你的帳號>/slot-price-watch.git
git push -u origin main
```

### 2. 開啟 GitHub Pages

repo → **Settings → Pages** → Source 選 `Deploy from a branch`，
Branch 選 `main`、資料夾選 `/docs` → Save。

幾分鐘後頁面就會出現在 `https://<你的帳號>.github.io/slot-price-watch/`。

### 3. 允許 Actions 寫回資料

repo → **Settings → Actions → General** → 最下面 Workflow permissions
選 **Read and write permissions** → Save。

（抓完的資料要 commit 回 repo，沒開這個會 push 失敗。）

### 4. 跑第一次

repo → **Actions** → 左邊選「比價抓取」→ **Run workflow**。

第一次建議先在 `limit_categories` 填 `3` 試跑，確認三站都抓得到東西
（約 5 分鐘），沒問題再跑全量。

---

## 「立即更新」按鈕

網頁右上角的按鈕會直接觸發一次抓取，不用等排程。有兩種模式：

**沒設 token** — 按鈕會開啟 GitHub Actions 頁面，你手動按 Run workflow。零設定，能用。

**設了 token** — 按鈕直接觸發，並在頁面上顯示進度（跑到第幾分鐘、即時紀錄連結），
跑完自動載入新資料。設定方式：

1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token
2. Repository access 只選這一個 repo
3. Permissions → Repository permissions → **Actions: Read and write**
4. 複製 token → 網頁右上角「設定」→ 貼上 → 儲存

Token 只存在你自己瀏覽器的 localStorage，不會進 repo、不會傳給第三方。
權限也只有「觸發這個 repo 的 Actions」，拿不到別的東西。

> 全量抓三站約需 **30–90 分鐘**（刻意放慢速度以免打擾站方），
> 按下去之後可以關掉分頁，跑完再回來看。

---

## 接到 Google 試算表

每次抓取都會產生三個 CSV，Google 試算表用 `IMPORTDATA()` 直接吃這些網址，
不需要 Google API 憑證、不需要 service account、不需要額外的自動化工具。

| 檔案 | 內容 |
|---|---|
| `docs/prices.csv` | 比價主表（機種、各站價格與連結、價差） |
| `docs/changes.csv` | 本次的價格變動（降價／漲價／上架／售完） |
| `docs/summary.csv` | Phase 0 的關鍵數字與結論 |

在試算表任一格貼上（把 `<帳號/專案名>` 換成你的）：

```
=IMPORTDATA("https://raw.githubusercontent.com/<帳號/專案名>/main/docs/prices.csv")
=IMPORTDATA("https://raw.githubusercontent.com/<帳號/專案名>/main/docs/changes.csv")
=IMPORTDATA("https://raw.githubusercontent.com/<帳號/專案名>/main/docs/summary.csv")
```

> **repo 必須是 Public。** `IMPORTDATA` 只讀得到公開網址，Private repo 的 raw 網址需要
> token，試算表拿不到，會一直顯示錯誤。

試算表大約每小時自己重抓一次。想立刻更新就重新整理分頁，或先去 GitHub 跑一次抓取。

---

## 排程

預設每日兩次（台灣時間 07:00 / 19:00），寫在 `.github/workflows/scrape.yml`。
要改頻率就改 cron（注意那裡是 UTC）。

價格是「不定期更改」，不是每小時改，所以每天兩次已經足夠。
拉高頻率只會增加被站方封鎖的風險，不會讓資料更有用。

---

## 本機執行

```bash
pip install -r requirements.txt

python -m scraper.main demo                      # 用假資料驗證流程，不連外網
python -m scraper.main run --limit-categories 3  # 快速試跑
python -m scraper.main run                       # 完整跑一輪
python -m scraper.main run --sites home_slot     # 只跑指定站
python -m scraper.main run --use-cache           # 重用本地快取（改比對邏輯時免重抓）
```

輸出：

| 檔案 | 內容 |
|---|---|
| `out/比價表_YYYYMMDD.xlsx` | 三張表：比價表 ／ Phase0 結論 ／ 待人工確認 |
| `docs/data.json` | 網頁用的資料 |
| `data/prices.db` | SQLite，含每一輪的價格歷史 |

---

## 機種比對是怎麼做的

同一台機在三站的寫法完全不同：

```
A-SLOT        SANKYO Lパチスロ からくりサーカス2 中古パチスロ実機［スマスロ］
ホームスロット   スマスロ からくりサーカス2
FC2           【中古】L からくりサーカス2 スマスロ 実機 コイン不要機付
```

處理流程在 `normalize.py` 和 `matcher.py`：

1. 拆括號 → 抽廠商 → 抽規格（スマスロ／L／6号機…）→ 刪雜訊詞 → 得到核心機種名
2. `rapidfuzz` 算相似度，**≥90 自動配對，75–90 丟人工確認，<75 視為不同機種**
3. 人工確認過的寫進 `data/aliases.json`，之後永久生效

有兩條硬規則不交給相似度決定，因為配錯的代價太高：

- **規格不同不配對** — L 版和舊版是不同機台，價格差好幾倍
- **續作編號不同不配對** — 「からくりサーカス」和「からくりサーカス2」文字相似度高達 96 分，
  但那是兩台完全不同的機器

寧可漏配（進待確認清單），也不要配錯（讓客戶看到假價差跑去買錯機台）。

---

## 網站改版了怎麼辦

爬蟲一定會壞，差別只在壞掉時你知不知道。這裡有兩層護欄：

- **商品數暴跌告警** — 某站抓到的數量比上次少一半以上，會寫進 `docs/data.json` 的
  warnings，網頁最上方跳黃色警告，Actions 摘要也會標記。不會安靜地把資料洗空。
- **分頁自動探測** — 分頁參數（`?p=` / `?page=` / …）是程式第一次跑時自己試出來的，
  記在 `data/pagination.json`。站方改了分頁方式的話，刪掉這個檔重跑就會重新探測。

解析器刻意不依賴 CSS class 名稱（那是改版第一個會變的東西），
改用「找出商品連結 → 往上找剛好包住它的最小容器 → 在裡面找價格」的結構性做法。

---

## 已知限制

- **FC2 站**偵查時 robots.txt 回 503，抓取節奏已放到最慢（每次請求間隔 4 秒）。
  如果仍抓不到，可能要改用瀏覽器模擬，屬於 Phase 1 的範圍。
- **比的是標價，不是到手價**。運費、コイン不要機／スマスロユニット 是否內含，
  各站規則不同，目前沒納入計算 —— 這是 Phase 2 的事。
- `data/prices.db` 每次抓取都會 commit，repo 會慢慢長大。以 PoC 的量級（每輪數千列）
  幾個月內都不成問題，真的變大再改成定期壓縮或只保留近 N 輪。

---

## 法務

只抓取各店家的**公開商品價格**供個人比價，不轉載商品圖片與商品描述，
抓取頻率遠低於一般使用者瀏覽。若之後要做成對外開放的比價網站（有流量或商業意圖），
性質就不同了，需要另外評估並標註來源、連回原站。
