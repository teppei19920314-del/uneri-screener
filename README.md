# うねり底スクリーナー

日本株の逆張り投資家向けの銘柄探しアプリです。市場の歪み(会社のせいではなく環境のせいで下がっている株)を、10年チャートのうねりの底で拾うためのものです。

平日21:17(JST)にGitHub Actionsが自動でデータを更新し、iPhoneのホーム画面から見るだけで使えます。費用はかかりません。

> 表示は銘柄探しの補助で、売買の推奨ではありません。最終判断は決算資料などで必ず確認してください。

## 用意するもの

- GitHubのアカウント(無料)
- J-Quantsのアカウント(無料プラン)
- 自分で決めるパスワード(画面を開くときに使います。長めのものにしてください)

パソコンで作業すると楽です。全部で20分ほどと、初回のデータ取得を待つ時間(約1時間)がかかります。

## 手順

### 1. J-Quantsに無料登録してAPIキーを取る

1. [J-Quants](https://jpx-jquants.com/) でユーザー登録し、Freeプランを選びます。
2. ダッシュボードの「APIキー」の画面でキーを発行してコピーします。あとで使います。

### 2. GitHubに公開リポジトリを作ってアップロードする

1. GitHubの右上の「+」→「New repository」を押します。
2. 名前(例: `uneri`)を入れ、**Public** を選んで「Create repository」を押します。
3. 「uploading an existing file」を押し、このフォルダの中身をすべてドラッグ&ドロップして「Commit changes」を押します。

**`.github` フォルダが漏れていないか確認してください。** 名前が「.」で始まるフォルダは隠しフォルダなので、Macでは見えず、ドラッグしたつもりで漏れることがあります。

- Macで隠しファイルを表示するには、Finderで `Command + Shift + .` を押します。
- リポジトリのトップに `.github` が見えなければ漏れています。その場合は「Add file」→「Create new file」を押し、ファイル名の欄に `.github/workflows/update.yml` と入力して、手元の `update.yml` の中身を貼り付けて保存します。

### 3. Secretsを登録する

リポジトリの「Settings」→「Secrets and variables」→「Actions」→「New repository secret」で、次の2つを登録します。

| 名前 | 中身 |
| --- | --- |
| `JQUANTS_API_KEY` | 手順1のAPIキー |
| `SITE_PASSWORD` | 自分で決めたパスワード |

`SITE_PASSWORD` がないと、平文で公開しないよう実行が止まります。

### 4. Pagesの公開元をGitHub Actionsにする

「Settings」→「Pages」→「Build and deployment」の「Source」を **GitHub Actions** にします。

### 5. 初回の実行

1. 「Actions」タブを開きます。ワークフローを有効にするか聞かれたら有効にします。
2. 左の「毎日の更新」→ 右の「Run workflow」→ 緑の「Run workflow」を押します。
3. 初回は約1時間かかります。緑のチェックになれば完了です。

2回目以降は取得済みのデータを引き継ぐので早く終わります。

### 6. iPhoneのホーム画面に追加する

1. iPhoneのSafariで `https://(GitHubのユーザー名).github.io/(リポジトリ名)/` を開きます。URLは「Settings」→「Pages」にも出ています。
2. パスワードを入れ、「このiPhoneに記憶する」にチェックしたまま「表示する」を押します。
3. 共有ボタン →「ホーム画面に追加」を押します。

以後は平日の夜に自動で更新されます。

## うまく動かないとき

まず「Actions」タブで失敗した実行(赤い×)を開き、赤くなっている手順を確認します。実行結果のページ下部の「データ更新の結果」に、データ源ごとの成否が出ます。

| 症状 | 確認すること |
| --- | --- |
| Actionsに「毎日の更新」が出ない | `.github/workflows/update.yml` がアップロードされているか(手順2) |
| 「SITE_PASSWORD が設定されていません」 | Secretsの名前のつづり(手順3) |
| 「JQUANTS_API_KEY が設定されていません」、HTTP 401/403 | Secretsの名前と、APIキーが有効か。キーを再発行したら登録し直します |
| 「Get Pages site failed」やデプロイの失敗 | PagesのSourceがGitHub Actionsになっているか(手順4) |
| 「銘柄数が少なすぎるため公開しません」 | Yahooのレート制限の可能性があります。時間をおいて「Run workflow」を押します。前回の公開はそのまま残ります |
| 信用残の欄に「○週分」と出る | 正常です。掲載が直近5営業日分だけなので、使い始めた日から貯めています |
| 財務が古い | 無料プランは直近12週のデータが取れません。最新の決算はアプリのリンクから株探で確認できます |
| パスワードが通らない | Secretsを変えたら、次の更新のあとで新しいパスワードが有効になります |
| 定期実行が止まった | 60日コミットがないと止まるので、50日ごとに空コミットしています。止まっていたら「Actions」で有効にし直します |

一部のデータ源が失敗しても、ほかのデータで更新は続きます。データは `actions/cache` に保存されており、消えても次回に自動で取り直します(信用残の蓄積だけは取り直せません)。

## 有料プランへの切り替え

J-Quantsのスタンダードプラン(月3,300円)にすると、財務の12週遅れがなくなり、株価・信用残・空売りもJ-Quantsから取るようになります。

1. J-Quantsでプランを変更します。
2. 「Settings」→「Secrets and variables」→「Actions」→「Variables」タブで、`DATA_SOURCE` という名前の変数を値 `jquants` で登録します。
3. 「Run workflow」を押します。初回は10年分の株価を取るため時間がかかります(長いときは複数回に分けて進みます)。

無料に戻すには、変数を消します。

## データについて

| データ | 出どころ | 頻度・範囲 |
| --- | --- | --- |
| 株価 | Yahoo!ファイナンス(yfinance) | 10年分の週足を毎回取り直し、直近1か月の日足も取得 |
| 財務・銘柄一覧 | J-Quants API V2 無料プラン | 15か月分。直近12週は取得不可 |
| 機関の空売り | JPX「空売りの残高に関する情報」 | 過去13か月分のExcel |
| 信用残 | JPX「銘柄別信用取引残高」 | 直近5営業日分のPDFを蓄積 |
| 米国の金利・景気 | FRED | CSV |

データはAES-256-GCMで暗号化して公開します(鍵はPBKDF2-SHA256・250,000回)。リポジトリに株価データは入りません。

## 手元で動かす

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python -m uneri.main --demo
.venv/bin/python -m http.server -d public-demo 8000
```

`--demo` はAPIキーなしで架空の銘柄を使って最後まで動かします。`http://localhost:8000/` で確認できます。

## ファイルの構成

| 場所 | 内容 |
| --- | --- |
| `.github/workflows/update.yml` | 毎日の自動実行 |
| `uneri/` | データ取得・計算・暗号化(`main.py` が入口) |
| `site/` | 画面(`index.html`=うねり底・人気株、`sector.html`=セクター) |
| `tests/` | テスト |
