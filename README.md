# YurOTS World Map

Statyczny, interaktywny viewer mapy OTBM przeznaczony do darmowego hostowania na GitHub Pages. Użytkownik odwiedzający stronę niczego nie wgrywa: ogląda mapę, sprite’y i spawny przygotowane wcześniej przez właściciela serwera.

Viewer obsługuje:

- mapę `.otbm` wraz z itemami i piętrami od `0` do `15`;
- mapowanie server ID → client ID z `items.otb`;
- sprite’y i animacje z `Tibia.dat` oraz `Tibia.spr`;
- potwory ze spawn XML i wyglądy z `creatures.xml`;
- outfity z kolorami head/body/legs/feet, addony i mounty;
- płynne przesuwanie, zoom, zmianę piętra, linki do konkretnej pozycji i inspektor pola.

## Dlaczego ładuje się lekko

Surowe pliki klienta nie trafiają na stronę. Lokalny konwerter:

1. odczytuje mapę oraz definicje;
2. wybiera tylko itemy, potwory i sprite’y faktycznie użyte na mapie;
3. usuwa zduplikowane obrazy;
4. zapisuje sprite’y w bezstratnych atlasach WebP;
5. dzieli mapę na przestrzenne chunki `64 × 64`, kompresowane gzipem;
6. tworzy prawdziwe, przezroczyste rendery itemów w rozdzielczości 8 px na kratkę do dalekiego zoomu.

Aktualny wynik dla `mapa.otbm` ma około **23 MiB** zamiast publikowania wszystkich surowych zasobów klienta. Około 14 MiB tej paczki to rzeczywisty, wyraźny podgląd mapy przy zoomie poniżej 50%. Przeglądarka pobiera na żądanie tylko widoczne chunki, kafle podglądu oraz potrzebne strony atlasu.

## Układ projektu

```text
docs/                 gotowa strona publikowana przez GitHub Pages
  assets/             wygenerowane, skompresowane dane mapy
tools/                konwerter OTBM/OTB/DAT/SPR
scripts/build.ps1     przebudowanie danych
scripts/serve.ps1     lokalny serwer testowy
tests/                test formatu i kompletności outputu
viewer.config.json    ścieżki i parametry konwersji
```

Pliki w `tools/vendor/` pochodzą z Twojego edytora i zostały użyte jako zgodne z nim, read-only parsery formatów klienta. Pozostała logika eksportu i renderowania jest oddzielona, żeby dało się ją łatwo rozwijać.

## Przebudowanie mapy

Wymagany jest Python 3.11+ oraz Pillow:

```powershell
cd E:\yurots\yurots\web-map-viewer
python -m pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

Źródła są skonfigurowane w `viewer.config.json`:

```json
{
  "paths": {
    "map": "../data/world/mapa.otbm",
    "spawns": "../data/world/mapa-spawn.xml",
    "creatures": "../creatures.xml",
    "otb": "../../master_sprotbdat/items.otb",
    "dat": "../../master_sprotbdat/Tibia.dat",
    "spr": "../../master_sprotbdat/Tibia.spr"
  }
}
```

Możesz zmienić te ścieżki, rozmiar chunków, atlasu i podglądów. `initialPosition` jest opcjonalne:

```json
"initialPosition": { "x": 1437, "y": 1231, "z": 11 }
```

Bez niego konwerter wybiera środek najgęściej zapełnionego fragmentu najczęściej używanego piętra.

## Test lokalny

Nie otwieraj `index.html` bezpośrednio jako `file://`, bo przeglądarka zablokuje moduły i pobieranie danych. Uruchom:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\serve.ps1
```

Następnie otwórz [http://localhost:8080/](http://localhost:8080/).

Testy:

```powershell
python -m unittest discover -s tests -v
```

## Darmowe wdrożenie na GitHub Pages

Na darmowym planie GitHub Pages repozytorium musi być **publiczne**. Nie publikujesz surowych `Tibia.spr`, `.dat`, `.otb` ani `.otbm`; publikujesz tylko gotowy katalog `docs`. Pamiętaj jednak, że atlasy nadal zawierają grafikę używaną przez mapę — umieszczaj je publicznie tylko wtedy, gdy masz do tego prawa.

### 1. Utwórz repozytorium

Na GitHubie wybierz **New repository**, nazwij je np. `yurots-map-viewer`, ustaw **Public** i nie dodawaj automatycznego README ani `.gitignore`.

### 2. Wyślij projekt

W PowerShellu, wewnątrz tego katalogu:

```powershell
cd E:\yurots\yurots\web-map-viewer
git init
git branch -M main
git add .
git commit -m "Add YurOTS map viewer"
git remote add origin https://github.com/TWOJ_LOGIN/yurots-map-viewer.git
git push -u origin main
```

Zastąp `TWOJ_LOGIN` swoim loginem. Jeśli Git poprosi o logowanie, użyj okna logowania Git Credential Manager.

### 3. Włącz Pages

W repozytorium przejdź do:

**Settings → Pages → Build and deployment → Source → GitHub Actions**

Plik `.github/workflows/deploy-pages.yml` automatycznie opublikuje katalog `docs`. Po zakończeniu akcji adres będzie miał postać:

```text
https://TWOJ_LOGIN.github.io/yurots-map-viewer/
```

### Aktualizacja po zmianie mapy, itemów lub looktype’ów

```powershell
cd E:\yurots\yurots\web-map-viewer
powershell -ExecutionPolicy Bypass -File .\scripts\update.ps1
git add docs
git commit -m "Update map"
git push
```

Przy pierwszym uruchomieniu na nowym komputerze dodaj instalację zależności:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\update.ps1 -InstallDependencies
```

`update.ps1` najpierw sprawdza wszystkie ścieżki źródłowe, następnie uruchamia pełną konwersję i testy, a na końcu podaje liczbę użytych itemów, looktype’ów, sprite’ów, atlasów oraz rozmiar outputu. Jeśli katalog jest repozytorium Git, pokazuje też pliki do wysłania.

Nie musisz ręcznie aktualizować żadnej listy:

- nowy item położony na mapie zostanie znaleziony po server ID w nowym `items.otb`, następnie po client ID w `Tibia.dat`;
- nowe klatki tego itemu zostaną pobrane z aktualnego `Tibia.spr`;
- nowy potwór ze spawn XML zostanie dopasowany po nazwie do `creatures.xml`, a jego `looktype` do `Tibia.dat`;
- atlas jest za każdym razem składany od nowa wyłącznie z aktualnie używanych sprite’ów;
- chunki i miniaturowy podgląd całej mapy są generowane ponownie z aktualnego `.otbm`.

Konwerter buduje wszystko w katalogu tymczasowym `docs/.assets.building`. Dopiero po poprawnym ukończeniu podmienia `docs/assets`, więc błąd odczytu nowego pliku nie usuwa poprzedniej, działającej wersji danych.

Każdy push na `main` ponownie wdroży stronę. Konwersję wykonujesz lokalnie, ponieważ prywatne pliki źródłowe nie znajdują się w repozytorium GitHub.

## Sterowanie

- przeciągnięcie lub strzałki — przesunięcie mapy;
- kółko myszy lub gest trackpada — zoom;
- `+` — piętro wyżej, czyli `Z−1`;
- `−` — piętro niżej, czyli `Z+1`;
- `Page Up` / `Page Down` — zmiana piętra z klawiatury;
- kliknięcie pola — lista itemów i potworów;
- podwójne kliknięcie — wycentrowanie bez zmiany zoomu;
- pola X/Y/Z — skok do pozycji;
- przycisk udostępniania — link zachowujący pozycję, piętro i zoom.

## Ważne ograniczenia

- Jest to viewer read-only, bez backendu i bazy danych.
- Spawny pokazują po jednym przedstawicielu potwora w miejscu wpisanym w spawn XML; nie symulują działającego serwera.
- Domyślnie potwory używają animacji idle. Opcję klatek chodu można włączyć w panelu warstw.
- „Widoczne piętra” pokazują tylko bieżącą kondygnację i poziomy poniżej: dla `Z≤7` jest to `Z…7`, a pod ziemią bieżące `Z` oraz maksymalnie `Z+1` i `Z+2`.
- Nieznana nazwa potwora w spawn XML jest pomijana i trafia do `manifest.json → warnings`.
- GitHub Pages jest publicznym hostingiem statycznym; nie nadaje się do ukrywania prywatnych zasobów.
