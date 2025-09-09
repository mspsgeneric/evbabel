# evtranslator/glossario.py
from __future__ import annotations
import re
from typing import List, Tuple, Dict, Optional


class Glossario:
    """
    Glossário bidirecional EN<->PT.
    Recursos:
      - Prioridade (maior primeiro) e termos mais longos primeiro (evita pegar substring).
      - Preserva caixa (UPPER/Title/lower) com base no termo encontrado na origem.
      - Dois modos de uso:
         * proteger/restaurar: recomendado (marca antes de traduzir, restaura depois).
         * aplicar(texto, tgt_lang): fallback (substitui no texto pronto; pode falhar se o termo sumir).
    Espera rows: [(termo_src, termo_dst, enabled, priority), ...]
    """

    def __init__(self) -> None:
        # Mapas de lookup por direção (chave original; usamos lower() no acesso)
        self._map_en2pt: Dict[str, str] = {}
        self._map_pt2en: Dict[str, str] = {}

        # Regex compilados por direção (para origem)
        self._pat_src_en: Optional[re.Pattern] = None  # procura termos em EN (termo_src)
        self._pat_src_pt: Optional[re.Pattern] = None  # procura termos em PT (termo_dst)

    # ---------------- utils ----------------

    @staticmethod
    def _needs_word_boundaries(s: str) -> bool:
        # Coloca \b se houver letras/dígitos (evita bater dentro de outras palavras)
        return any(ch.isalnum() for ch in s)

    @staticmethod
    def _shape(dst: str, found: str) -> str:
        """Ajusta caixa do dst conforme o 'found' na origem."""
        if not found:
            return dst
        if found.isupper():
            return dst.upper()
        if found.islower():
            return dst.lower()
        if found[0].isupper() and found[1:].islower():
            return dst[:1].upper() + dst[1:]
        return dst

    @staticmethod
    def _compile_pattern(terms: List[str]) -> Optional[re.Pattern]:
        if not terms:
            return None
        # ordena por tamanho desc para favorecer match de termos mais longos
        terms = sorted(terms, key=len, reverse=True)
        tokens = []
        for t in terms:
            esc = re.escape(t)
            if Glossario._needs_word_boundaries(t):
                tokens.append(rf"\b{esc}\b")
            else:
                tokens.append(esc)
        return re.compile("|".join(tokens), flags=re.IGNORECASE)

    # ---------------- build ----------------

    def carregar(self, rows: List[Tuple[str, str, int, int]]) -> None:
        ativos = [(a, b, int(en), int(p)) for (a, b, en, p) in rows if int(en) == 1]
        # prioridade desc; para empates, termo_src mais longo primeiro
        ativos.sort(key=lambda x: (-x[3], -len(x[0])))

        # Direção EN->PT
        self._map_en2pt = {src: dst for (src, dst, _en, _p) in ativos}
        en_terms = [src for (src, _dst, _en, _p) in ativos]

        # Direção PT->EN (espelho)
        self._map_pt2en = {dst: src for (src, dst, _en, _p) in ativos}
        pt_terms = [dst for (_src, dst, _en, _p) in ativos]

        # Padrões para procurar NA ORIGEM
        self._pat_src_en = self._compile_pattern(en_terms)  # quando src_lang == 'en'
        self._pat_src_pt = self._compile_pattern(pt_terms)  # quando src_lang == 'pt'

    # ---------------- proteger/restaurar (recomendado) ----------------

    def proteger(self, texto: str, src_lang: str, tgt_lang: str):
        """
        Substitui termos da ORIGEM por placeholders (__EVG0__, __EVG1__...) e
        retorna (texto_marcado, lista_de_tags).
        Cada tag = (placeholder, string_final_para_inserir_no_DESTINO).
        """
        if not texto:
            return texto, []

        # Seleciona padrão e mapa conforme a direção
        if src_lang == "en" and tgt_lang == "pt":
            pat = self._pat_src_en
            mapping = self._map_en2pt
        elif src_lang == "pt" and tgt_lang == "en":
            pat = self._pat_src_pt
            mapping = self._map_pt2en
        else:
            # outras línguas: não faz nada
            return texto, []

        if pat is None or not mapping:
            return texto, []

        tags = []
        idx = 0

        def repl(m: re.Match) -> str:
            nonlocal idx
            found = m.group(0)
            lower = found.lower()

            # lookup case-insensitive
            dst = None
            for k, v in mapping.items():
                if k.lower() == lower:
                    dst = v
                    break
            if dst is None:
                # fallback: mantém original
                return found

            # ajusta caixa do destino com base em como apareceu na origem
            dst_shaped = Glossario._shape(dst, found)

            placeholder = f"__EVG{idx}__"
            tags.append((placeholder, dst_shaped))
            idx += 1
            return placeholder

        marcado = pat.sub(repl, texto)
        return marcado, tags

    def restaurar(self, texto: str, tags: List[Tuple[str, str]]) -> str:
        """Troca placeholders pelas strings finais."""
        if not texto or not tags:
            return texto
        out = texto
        for ph, final in tags:
            out = out.replace(ph, final)
        return out

    # ---------------- aplicar (fallback) ----------------

    def aplicar(self, texto: str, tgt_lang: str) -> str:
        """
        Fallback: substitui no texto pronto (pode não encontrar os termos).
        Mantida para compatibilidade.
        """
        if not texto:
            return texto

        if tgt_lang == "pt":
            pat = self._pat_src_en
            mapping = self._map_en2pt
        elif tgt_lang == "en":
            pat = self._pat_src_pt
            mapping = self._map_pt2en
        else:
            return texto

        if pat is None or not mapping:
            return texto

        def repl(m: re.Match) -> str:
            found = m.group(0)
            lower = found.lower()
            dst = None
            for k, v in mapping.items():
                if k.lower() == lower:
                    dst = v
                    break
            if dst is None:
                return found
            return Glossario._shape(dst, found)

        return pat.sub(repl, texto)
