-- Task 23 item 1: the lexical half of hybrid retrieval.
--
-- Azerbaijani has no Postgres text-search dictionary, so there is no stemming to
-- lean on. Two substitutes carry it instead: `:*` prefix terms in the tsquery
-- (which is what buys `kredit` -> krediti / kreditin / kreditler on an
-- agglutinative language), and trigram similarity against the short document
-- header, which tolerates the missing diacritics real users type.
--
-- `fold` strips Azerbaijani diacritics so `neceden` matches `nəçədən`. It is a
-- STORED generated column rather than an expression index because the tsquery
-- side folds too, and both sides must agree character-for-character.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE rag.chunks ADD COLUMN IF NOT EXISTS fold text
  GENERATED ALWAYS AS (translate(lower(embed_input), 'əıöüçşğ', 'eioucsg')) STORED;

CREATE INDEX IF NOT EXISTS chunks_fold_fts
  ON rag.chunks USING gin (to_tsvector('simple', fold));
