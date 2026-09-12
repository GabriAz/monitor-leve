# Contribuindo com o monitor leve

Obrigado pelo interesse em contribuir! 🍂

## Como reportar um bug

1. Abra uma issue descrevendo: o que esperava, o que aconteceu, o Windows (10/11),
   e se usa o `.exe` instalado ou roda do código-fonte.
2. Inclua o máximo de contexto possível (o `config.json` **não** — ele é local).

## Como propor uma mudança

1. Abra um branch a partir de `master`.
2. Faça mudanças pequenas e focadas (uma coisa por commit).
3. Mantenha o estilo: comentários em **pt-BR**, sem "caveman" (frases completas).
4. Valide antes de abrir o PR:

```bash
python monitor_bar.py          # roda e confere o comportamento
python -m py_compile monitor.py monitor_bar.py   # checa sintaxe
```

5. Descreva o "porquê" no corpo do PR, não só o "o quê".

## Estrutura

| Arquivo | Papel |
|---------|-------|
| `monitor.py` | Camada de coleta (psutil + WMI + PresentMon p/ FPS) |
| `monitor_bar.py` | Camada de UI (tkinter Canvas + AppBar + Mica) |
| `build-instalador.bat` | Build completo (exe + instalador) |
| `installer.iss` | Inno Setup (instalação sem admin) |
| `config.json` | Estado do usuário (não versionado) — ver `.gitignore` |

## Notas de design

- **Dois loops de coleta** (rápido 1 Hz + lento) pra barra nunca travar o clique.
- **AppBar usa o rect `mon`, não `work`** — re-registrar `ABM_NEW` repetido faz a
  barra "subir"; usar `mon` + `ABM_SETPOS` evita.
- **Config é frozen-aware** — no `.exe` (onefile), `__file__` aponta pra um
  diretório temporário que some ao sair; por isso o `config.json` vai para junto
  de `sys.executable`.
- **FPS é opcional** — sem o `PresentMon.exe` ao lado (ou sem `Performance Log
  Users`), o módulo simplesmente fica `n/d` e é ocultado.

## Código de conduta

Seja gentil. Críticas ao código, não à pessoa. Mantemos o tom que o próprio app
anuncia: "feito com muito ♥ e ☕".