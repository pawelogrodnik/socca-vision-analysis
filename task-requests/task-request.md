To jest plik do zadan, nie zawsze typowo koderskich, ktore potrzebujemy wykonac.

# Task 1

![alt text](image.png) - potrzebuje gdzies informacji 'jakie' wideo jest aktualnie w analizie/review; mozemy przy 1 kroku (po jego 'przejsciu') dodac dodatkowa informacje z nazwa meczu i pliku

# Task 2

![alt text](image-1.png) - potrzebuje zeby ta lista byla sortowana od gory od 'najnowszego' wideo + te karty meczowe powinny miec na sobie rowniez nazwe pliku wideo z ktorego jest analiza.

# Task 3

Obsluga wielu pilek widocznych jednoczesnie. W nagraniu moga pojawic sie dwie lub wiecej poprawnie wykrytych pilek, np. gdy pilka z sasiedniego boiska wpadnie w kadr albo chwilowo pozostanie na analizowanym boisku. Detektor powinien zachowac wszystkie poprawne kandydatury, ale dalsza logika musi wybrac i stabilnie utrzymywac jedna pilke aktywna w grze.

Priorytety wyboru pilki aktywnej:

- ciaglosc dotychczasowego toru ruchu i brak niemozliwego skoku,
- zgodnosc z ostatnim posiadaniem, podaniem, kontaktem lub restartem,
- pozycja na analizowanym boisku i zwiazek z aktualna akcja zawodnikow,
- utrzymanie kandydatury przez kilka kolejnych klatek zamiast natychmiastowej zmiany,
- mozliwosc oznaczenia pozostalych wykrytych pilek jako `inactive/secondary`, bez traktowania ich jako false positive modelu.

Zmiana aktywnej pilki powinna wymagac mocnego potwierdzenia. W artefaktach debugowych nalezy zachowac powod wyboru, confidence oraz informacje o konkurencyjnych kandydatach. Statystyki posiadania, podan, restartow i momentum moga korzystac tylko z pilki oznaczonej jako aktywna w grze.

# Task 4
render meczu 'pod klienta' - gdzie po oznaczeniu stintow czy w jakikolwiek inny sposob juz po przypisania player id do faktycznej osoby renderujemy mecz gdzie gracze sa podpisani z imienia;