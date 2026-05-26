from __future__ import annotations

import voyager.utils as U


class CurriculumQACache:
    def __init__(self, *, qa_cache, vectordb, cache_path):
        self.qa_cache = qa_cache
        self.vectordb = vectordb
        self.cache_path = cache_path

    def get_exact_answer(self, question):
        if question in self.qa_cache:
            return question, self.qa_cache[question]
        return None

    def get_similar_answer(self, question, *, max_score=0.05):
        if self.vectordb._collection.count() <= 0:
            return None
        docs_and_scores = self.vectordb.similarity_search_with_score(question, k=1)
        if docs_and_scores and docs_and_scores[0][1] < max_score:
            question_cached = docs_and_scores[0][0].page_content
            if question_cached in self.qa_cache:
                return question_cached, self.qa_cache[question_cached]
        return None

    def store_answer(self, question, answer):
        if question in self.qa_cache:
            self.qa_cache[question] = answer
            U.dump_json(self.qa_cache, self.cache_path)
            self.vectordb.persist()
            return
        self.qa_cache[question] = answer
        self.vectordb.add_texts(texts=[question])
        U.dump_json(self.qa_cache, self.cache_path)
        self.vectordb.persist()
