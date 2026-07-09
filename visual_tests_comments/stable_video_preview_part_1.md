
Analiza 0-10minuta overlay

# klatka 00000
tu juz mamy blad na starcie - - zawodnik A07 zostal przypisany do przeciwnej druzyny (bazujac na reszcie zawodnikow druzyny A ktorzy sa w druzynie A). A07 powinien byc w druzynie B jako bramkarz. ![alt text](image-6.png)

zawodnicy z pola na klatce wydaja sie poprawnie przypisani wiec podaje frame-0 przypisania ID per zawodnik i na podstawie tego bede mowil o zmianach/bledach w dalszej czesci pliku
A01 - Piotrek
A02 - Przemek
A03 - Patryk
A04 - Mateusz
A05 - Krzysiek
A06 - Kuba
A07 - tu powinien byc nasz GK ale jest przypisany na overlayu bramkarz druzyny przeciwnej

A10 - pierwsza occurency zmiany, wchodzi Pawel i dostaje to ID jako pierwsze wiec mu to przypisuje

wszedzie tam gdzie pisze 'nasz' oznacza to team A - to druzyna w ktorej gram i dlatego tak pisze;

zawodnicy druzyny B z pola rowniez sa poprawnie rozpoznani - ich nie przypisujemy do prawdziwych osob w tym przypadku bo mnie nie interesuja;

teraz rozpisze swoje uwagi per klatka (bierz pod uwage, ze to jest test wizualny; czyli rozpisuje numer klatki gdzie ja to zauwazylem, w rzeczywistosci to moze byc juz wczesniej; rownoczesnie nie twierdze ze jestem w stanie opisac tu wszystko, niektore rzeczy moga mi umknac).

moje uwagi ogolne (do zweryfikowania);
1) poczatek chyba sie rozjechal przez bledne oznaczenie bramkarza druzyny przeciwnej, przez co na sile probowalismy przypisanc jednego brakujacego niebieskiego, plus w 'naszej' druzynie detektor mylnie wykrywal 7 zawodnikow, mimo ze poprawnie bylo oznaczonych 6 naszych i GK druzyny przeciwnej
2) pitch overlay nie pasuje do realnego boiska co wedlug mnie tez powoduje problemy z niektorymi przypisaniami ID bo pewnie analizie wydaje ise, ze gracz jest poza boiskiem, gdy w rzeczywistosci tak nie jest;
3) chyba musimy czasami pozwolic wykryc tylko '6' zawodniokw, czesto jest tak ze zawodnik wychodzi poza boisko idac po prostu po pilke, do wznowienia z roznego, autu, czy od bramkarza. teraz wydaje sie na sile przypisywac byle kogo byle by bylo 7 zawodnikow jezeli jeden znika;

# klate 1489
nasz GK pojawia sie jako A01 zamiast Piotrka ; Piotrek znika z detekcji; A07 to ciagle bramkarz druzyny przeciwnej

# 1886 
nasz GK pojawia sie jako B11? A01 wraca do Piotrka 

# 1896
Piotrek znowu znika; nasz GK oznaczony jako A01

# 1947
Krzysiek znika, A05 zostaje przypisane Piotrkowi, B11 ciagle jako nasz GK; jeden z zawodnikow druzyny B oznaczony jak RAW? w srodku boiska

# 2186

Patryk znika, A03 pojawia sie nad Krzyskiem, A05 wydaje sie ustabilizowac na Piotrku, A01 to nasz GK (aktualnie jest juz rozjazd miedzy Patryk-Krzysiek-Piotrek-nasz GK)

# 2407
B05 z druzyny B poajwia sie z "?" wychodzi poza pitch zaznaczony przez nasz pitch config, ktory zaczal sie 'rozjezdzac' wzgledem realnego pitch! [alt text](image.png)

# 2788 
Kuba znika z detekcji, A06 zostaje przypisane przeciwnikowi ktory byl obok niego, Patryk tez nie jest w detekcji, bramkarz druzyny przeciwnej ciagle jest oznaczony jako A07 zamiast byc w druzynie B 

# 3060
Kuba wraca jako A06, Patryk ciagle zgubiony, Mateusz z A04 skacze na A04?

# 3256

Piotrek zostaje przypisany do A02?, Przemek znika z detekcji, pojawia sie rowniez B02?

# 3430
Przemek wraca do A02?, Piotrek znika z detekcji

# 3940
Piotrek wraca do A01, Przemek do A02, Krzysiek ma A03, Patryk A05 (kontynuacja swapa z wczesniej), Mateusz ma stabilnie A04 od poczatku, Kuba A06, nasz bramkarz znika z detekcji

# 5281
Mateusz znika z detekcji, nasz bramkarz ciagle bez detekcji

# 5526
nasz bramkarz pojawia sie jako A08

# 5806
Mateusz pojawia sie jako A08

# 5918
Mateusz znika z detekcji, nasz Gk jako A08

# 6057
Patryk znika z detekcji

# 6203
gk druzyny przeciwnej w koncu gubi oznaczenie druzyny A, Patryk pojawia sie jako A07 (swap z bramkarzem druzyny przeciwnej, Mateusz pojawia sie jako A09) - w koncu wydaje sie ze druzyny sa poprawnie przypisane (tzn zawodnicy sa poprawnie przypisani do danej druzyny)

# 6502 
Mateusz wydaje sie ustabilizowac jako A09 z wyjsciowego A04, Piotrek pojawia sie jako A01?

# 6574
teraz nasz bramkarz przejmuje oznaczenie B i pojawia sie jako B03

# 6673
nasz bramkarz pojawia sie jako A08, Kuba znika z detekcji, pitch jest poprawnie nalozony na boisko ![alt text](image-1.png), pojawia sie zawodnik B01?

# 6840
zawodnik druzyny przeciwnej pojawia sie jako A08 podczas gdy nasz bramkarz jest 'poza boiskiem' bo po prostu idzie po pilke, ktora wypadla poza boisko zeby wznowic gre; Patryk to A07

# 6949
nasz GK wraca jako A08, zawodnik ktory byl blednie oznaczony z druzyny B do druzyny A znika z detekcji

# 7430
boisko znowu sie rozjechalo ![alt text](image-2.png)

# 7179
boisko ciagle sie rozjezdza i nie wraca do poprawnej formy ![alt text](image-3.png)

# 8207
zawodnik druzyny przeciwnej zniknal kompletnie z detekcji (zawodnik obok A02, powyzej A02 oraz ponizej A09)

# 8484
nasz naszym Gk pojawiaja sie 2 bboxy, jeden B01 niebieski i drugi, pod spodem pomaranczowy, nie widze niestety ID bo jest przykryte

# 8573
nasz zawodnik dostaje A08, niebieski bbox znika

# 8757
Patryk dostaje A01, Piotrek ma A07?, Mateusz A09?

# 9130
Patryk wraca do A07, Piotrek do A01, Mateusz do A09

# 9563
Kuba dostaje A10

# 9667
Piotrek dostaje RAW? zamiast przypisania, pare zawodnikow druzyny niebieskiej jako B03?

# 9750
Piotrek wraca jako A01

# 10092

pierwsza zmiana - Kuba schodzi ktory byl oznaczony przed zmiana jako A10, wchodzi Pawel ktory dostaje A10

# 10756 
Pawel ma ustabilizowane A10 co chyba jest OK, nasz GK dostaje A11

# 11886
widac ze nagranie z drona delikatnie zmienilo movement, lekko chyba sie odchylilo w lewo bez reakcji pitch overlaya ktory nie poprawnie pokazuje boisko; Mateusz znika z detekcji, A09 przejmuje przeciwnik

# 11946
A09 wraca do Mateusza

# 12189
kolejna zmiana, schodzi Mateusz, wchodzi Andrzej ktory dostaje A01, Przemek dostaje A09 po Mateuszu, nasz GK dostaje A11?, Pawel stabilnie A10

# 16014
A13 pojawia sie nad Krzyskiem, na boisku mamy aktualnie A01 Andrzej; A09 Przemek; A02 Piotrek; A11 nasz GK; A10 Pawel; A07 Roman (gdzies mi umknela trzecia zmiana, Roman wszedl za Patryka); A13 Krzysiek

# 16133 - 16190
boisko jest idealnie ustawione zgodnie z liniami boiska ![alt text](image-4.png), podczas gdy nagle 'skacze' overlay boiska (nie ma smooth przejscia) i pokazuje znowu blednie ![alt text](image-5.png) 

# 17685
Roman pojawia sie jako A14, Pawel jako A10? ale nie gubi samego ID odkad wszedl na boisko

koniec pierwszych 10 minut;













