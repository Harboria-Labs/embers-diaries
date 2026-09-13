# Lobby design (Features 10 / 11 / 25 / 27; 26 deferred)

Status: implemented on `main` (request/response board). Realtime (§26) still deferred.
Tool: `ember_lobby` actions `open | publish | corroborate | board | promote | close`.
Posts are not Ember records. Room gate is default-deny. Off until `action=open`.

Lobby is an on-demand work board for a shared task. It is not chat,
not memory, and not on by default.
