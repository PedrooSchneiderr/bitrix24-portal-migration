# bitrix24-portal-migration

Toolkit em Python para migrar dados de CRM entre dois portais Bitrix24 usando só a REST API
(webhooks de entrada) — empresas, contatos, negócios, funis/etapas, campos personalizados,
produtos dos negócios e arquivos.

Extraído da arquitetura que desenhei para uma migração real de ~3.000 negócios, validada em
6 rodadas de teste ponta a ponta contra um portal sandbox. Este repositório é uma versão
genérica e limpa — sem dados nem configuração de cliente.

## O problema

Bitrix24 não tem export/import "portal → portal". Pela REST, cada registro precisa ser recriado
chamada a chamada, e os IDs mudam todos: usuários, funis, etapas, campos `UF_CRM_*` e opções de
lista têm identificadores diferentes em cada portal. Pior: várias chamadas **falham em silêncio**
(HTTP 200, sem erro, dado não gravado). O toolkit trata cada uma dessas armadilhas no código.

## Arquitetura

**Crosswalk** (`crosswalk.py`): um JSON persistente `tipo → {id_origem: id_destino}` para
entidades, campos, opções de lista, funis e etapas. É consultado antes de toda criação, então
o processo é idempotente (rodar de novo não duplica) e retomável depois de uma queda.

**Ordem de migração** (respeita dependências):

1. `fields` — só os campos personalizados que têm valor nos registros + campos auxiliares de responsável
2. `pipelines` — funis e etapas
3. `companies` → `contacts` (+ vínculo empresa↔contato)
4. `deals` (+ vínculos N:N com contatos e product rows)
5. `files` — passe dedicado para campos do tipo arquivo
6. `verify` — relê origem e destino via API e gera CSV de conferência

Usuários não são criados (a REST não permite): são resolvidos por e-mail no destino.

```
bitrix_client.py   cliente REST: throttle, retry, paginação, batch, download de arquivo
crosswalk.py       mapa origem→destino em JSON
migrate.py         orquestrador (--dry-run, --only)
steps/             fields, pipelines, users, records, verify
```

## Gotchas reais tratados no código

- **Paginação com `start=`** trunca em bases grandes → `list_all` pagina por `filter[>ID]` ordenado por ID.
- **`QUERY_LIMIT_EXCEEDED`** derruba cargas longas → throttle de 0,5s por chamada e retry com backoff exponencial.
- **`batch` retorna 200 mesmo com sub-requisições falhando** → `batch()` checa `result_error` de cada item e levanta erro.
- **Nomes `UF_CRM_*` são gerados por portal** → campos são casados pelo rótulo e mapeados no crosswalk, nunca copiados pelo nome.
- **Portais antigos têm centenas de campos mortos** → só os campos com valor nos registros migrados são recriados.
- **`STATUS_ID` = `C<funil>:<CODE>` e o funil sempre difere** → toda etapa passa pelo crosswalk, casada por nome.
- **Etapa comum com `SORT` depois da etapa de ganho é recusada** → a etapa WON é "estacionada" em SORT alto e restaurada no fim.
- **Etapa com `SEMANTICS` (S/F) com SORT baixo é recusada** → é criada com SORT provisório alto e corrigida num passe final.
- **`filter[PHONE]`/`filter[EMAIL]` em `*.list` pode retornar o registro errado sem erro** → dedup via `crm.duplicate.findbycomm`.
- **`findbycomm` responde dict quando acha e lista vazia quando não acha** → o código checa o tipo antes de ler.
- **`crm.deal.contact.items.set` substitui a lista inteira** → sempre envia o conjunto completo de contatos de uma vez.
- **Product row com `PRODUCT_ID` 0 é texto livre** → linha preservada com nome/preço/quantidade; produto sem mapeamento vira linha "congelada" igual.
- **Campo arquivo copiado como valor comum fica vazio sem erro** → arquivos ficam fora da tradução genérica e passam por download + reupload dedicado.
- **URL `show_file.php` devolve a tela de login no webhook** → download via `crm.controller.item.getFile`.
- **Escrita de arquivo só funciona no formato `[{"fileData": [nome, base64]}]`** → `file_payload()` gera exatamente esse formato.
- **Responsável inexistente no destino faz tudo cair no dono do webhook** → resolução por e-mail com fallback, aplicada em *todas* as entidades, mais campos auxiliares com nome/e-mail original.
- **Negócio apontando para empresa já apagada na origem** → não inventa vínculo; registra para revisão como achado de qualidade de dados.
- **"Sucesso" na resposta não prova que gravou** → `verify` relê os dois lados e acusa campos de arquivo vazios.

## Como rodar

```bash
pip install -r requirements.txt
cp .env.example .env        # preencha os dois webhooks e o usuário de fallback
python migrate.py --dry-run # lê tudo, não grava nada, imprime o que faria
python migrate.py           # carga real
python migrate.py --only deals files verify   # retomar etapas específicas
```

> **Antes da carga real: desative as automações do portal de destino** (regras de automação,
> processos de negócio, bots, webhooks de saída para Make/n8n/etc.). Cada registro criado dispara
> os eventos de "novo negócio/contato" como se fosse novo — e-mails, SLAs e integrações rodariam
> sobre anos de histórico. Reative depois.

Recomendações: teste primeiro em um portal sandbox, com um lote pequeno, e confira o CSV de
`verify` antes de escalar.

## Fora do escopo desta versão

Leads, smart processes (SPA), atividades/timeline e histórico de Canal Aberto/WhatsApp seguem o
mesmo padrão (crosswalk + passe dedicado), mas não estão implementados aqui.

## Autor

**Pedro Schneider** — engenheiro de integrações Bitrix24
[LinkedIn](https://www.linkedin.com/in/pedro-schneider-6b372518b/)

Licença MIT.
