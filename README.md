# WSEI Calendar Sync — IS AI, III semestr

Automatyczny kalendarz ICS dla toku:

- **IS/WSEI N mgr inż. 1.5 2025/2026 zima**
- Wydział Studiów Stosowanych w Krakowie
- Informatyka stosowana, niestacjonarne
- specjalność: Sztuczna inteligencja
- III semestr, zima 2026/2027

## Subskrypcja kalendarza

Stały adres ICS:

`https://raw.githubusercontent.com/Sskyem/wsei-plan/main/docs/plan.ics`

Ten URL należy dodać jako **subskrypcję kalendarza**, a nie importować plik jednorazowo.

### iPhone / iPad
Ustawienia → Aplikacje → Kalendarz → Konta kalendarza → Dodaj konto → Inne → Dodaj subskrybowany kalendarz.

### Google Calendar / Android
Najwygodniej na komputerze: Google Calendar → Inne kalendarze → Z adresu URL → wklej powyższy adres. Kalendarz pojawi się potem na telefonie na tym samym koncie.

## Jak działa automat

Źródło:
`https://harmonogram.krakow.ideis.pl/Plany/PlanyTokow/1079`

GitHub Actions sprawdza plan co 3 godziny. Skrypt pobiera eksport CSV z Wirtualnego Dziekanatu, filtruje wyłącznie moje grupy i przedmioty, generuje `docs/plan.ics` i zapisuje zmianę tylko wtedy, gdy plan faktycznie się zmieni.

Grupy:
- `konw/2/IS-AI IIIsemN`
- `lab/2/IS-AI IIIsemN`
- `konw/2/IS-MGR IIIsemN`

Przedmioty:
- AI w biznesie
- Firma symulacyjna
- Głębokie sieci neuronowe (Deep Learning) – projekt
- Kryptologia
- Zaawansowane usługi systemów linuxowych

Zakres semestru: **2026-10-01 — 2027-02-14**.

Jeśli serwer uczelni zwróci błąd lub po filtrowaniu nie zostanie znalezione żadne zajęcie, skrypt kończy się błędem i nie nadpisuje ostatniego poprawnego kalendarza pustym plikiem.

## GitHub Pages — opcjonalnie

Repo zawiera też katalog `docs/`. Jeśli włączysz GitHub Pages z gałęzi `main` i katalogu `/docs`, strona będzie dostępna pod:

`https://sskyem.github.io/wsei-plan/`

a kalendarz pod:

`https://sskyem.github.io/wsei-plan/plan.ics`

Do działania subskrypcji GitHub Pages nie jest wymagany — adres `raw.githubusercontent.com` działa od razu.

## Prywatność

Repozytorium jest publiczne, więc opublikowany plan również jest publiczny dla osoby znającej adres. Projekt nie przechowuje loginu ani hasła do USOS; korzysta wyłącznie z publicznego harmonogramu uczelni.
