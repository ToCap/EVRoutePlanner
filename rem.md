le left panel est un peu trop large ce qui rréduit l'affichage du layout principal, je dirais qu'il faut le raccourcir de 0.5cm. Ainsi le Détail par tranche ne sera pas coupé à l'affichage
Bug : Le calcul pour trouver les brones de recharges n'est lancé que si l'utilisateur va dans l'onglet Bornes de recharges dans la parti basse du layout principal. Il faut le lancer quand l'utilisateur clique sur le bouton Calculer. Il faut ajouter un paramêtre dans l'onglet Configuration pour autoriser la recherche de borne de recharge en arrière plan une fois l'itinéraire calculé, si la paramêtre n'est pas coché alors on lance la recher de bornes en même temps que celle de l''itinéraire
ENH : ajouter un paramêtre dans l'onglet Bornes pour activer La recherche de borne sur tout l'itinéraire ou seulement s'il y en a besoin (avant d'atteindre le seuil minimum)
ENH : l'information "Résultat affiné (types de route vérifiés tous les km hors autoroute)." doit appaitre sous la forme d'une notification seulement et éventuellement avoir un bouton de type Badges pour réafficher les dernières notifications. On ne garde que les 3 dernières lees plus importantes. à raffiner ensemble
ENH : Dans l'onglet Trajet, ajouter un InputGroup pour Itinéraire et un InputGroup pour Charge
ENH : Ajouter dans Energie, dans l'InputGroup Recharge, un Multi-Value Dropdown pour permettre de sélectionner le type de chargeur supportée
100kW DC (350A)borne charge rapide
11kW AC (3x16A)borne de recharge domestique
22kW AC (3x32A)borne de recharge domestique
7,4kW AC (32A)borne de recharge domestique
3,7kW AC (16A)prise domestique renforcée
2,3kW AC (10A)prise domestique
ENH : Ajouter dans Energie un InputGroup Recharge avec des contrôles pour saisir une durée pour chaque type de chargeur :
100kW DC (350A)borne charge rapide
11kW AC (3x16A)borne de recharge domestique
22kW AC (3x32A)borne de recharge domestique
7,4kW AC (32A)borne de recharge domestique
3,7kW AC (16A)prise domestique renforcée
2,3kW AC (10A)prise domestique
Si un ou plusieurs type de chargeur n'est pas sélectionné via le Multi-Value Dropdown de l'InputGroup Recharge, alors le contrôle de saisie de durée est grisé
ENH : refaire le layout de résultat en un seul bloc. les Résultats sur la gauche des onglets sont intégrés dans un nouvel onglets Trajet calculé. Ce nouvel onglet présente le résultat (exemple "312.8 km · 201 min (OSRM) ville 6.1 km · route 20.2 km · autoroute 286.5 km") et les trsois scénarios de gauche à droite. à raffiner ensemble

ENH : dans la recherche de bornes, utiliser le ou les types de chargeur sélectionné par l'utilisateur comme filtre. Si l'information n'est pas renseigné par l'utilisateur, ne pas filtrer les bornes disponibles. Si l'information n'est pas disponible via la requete, ajouter une icone danger ou équivalent à côté de la ligne dans la liste des bornes sélectionnées