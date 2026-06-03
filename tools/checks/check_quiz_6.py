import sys
sys.path.insert(0, '.')
from app import pg_get_quiz_by_id

quiz = pg_get_quiz_by_id('6')
if quiz:
    print('Quiz 6 found:')
    title = quiz.get('title')
    atype = quiz.get('attempts_type')
    alimit = quiz.get('attempts_limit')
    print(f'  Title: {title}')
    print(f'  attempts_type: {atype}')
    print(f'  attempts_limit: {alimit}')
else:
    print('Quiz 6 not found')
