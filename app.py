# -*- coding: utf-8 -*-

from flask import Flask, render_template, request, jsonify
import pandas as pd
import re
from datetime import datetime
import io
import os
import json
import firebase_admin
from firebase_admin import credentials, auth, firestore

app = Flask(__name__)
db = None
try:
    # ... (bloco de inicialização do Firebase sem alterações) ...
    creds_json_str = os.environ.get('FIREBASE_CREDENTIALS_JSON')
    if creds_json_str:
        creds_dict = json.loads(creds_json_str)
        cred = credentials.Certificate(creds_dict)
    else:
        cred = credentials.Certificate('firebase-credentials.json')
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase Admin SDK e Firestore inicializados com sucesso.")
except Exception as e:
    print(f"ERRO: Falha ao inicializar o Firebase Admin SDK: {e}")

# vvvvvv FUNÇÃO DE PARSE ATUALIZADA PARA NÃO IGNORAR LINHAS vvvvvv
def parse_data_file(file_content):
    lines = file_content.split('\n')
    records = []
    icao_code = 'SBIZ'
    data_date = None
    expected_total = 0

    # 1. Procura o cabeçalho para extrair o total esperado
    for line in lines:
        if line.strip().startswith('SBIZAIZ0'):
            try:
                # Extrai os últimos 5 dígitos da linha do cabeçalho
                expected_total = int(line.strip()[-5:])
            except (ValueError, IndexError):
                pass
            break # Encontrou o cabeçalho, pode parar de procurar

    # 2. Processa todas as linhas que parecem ser de voo
    for line in lines:
        # Mantém apenas um filtro básico para linhas de cabeçalho ou muito curtas/vazias
        if line.strip().startswith('SBIZAIZ0') or len(line.strip()) < 20:
            continue

        record = {
            'timestamp': None, 'matricula': 'N/A', 'tipo_aeronave': 'N/A',
            'origem': 'N/A', 'destino': 'N/A', 'regra_voo': 'N/A', 
            'pista': '', 'responsavel': 'N/A', 'flight_class': 'N/A'
        }

        try:
            # Tenta extrair a matrícula, que tem posição mais fixa
            record['matricula'] = line[15:22].strip()

            # Tenta encontrar a regra de voo, mas não descarta a linha se não encontrar
            rule_match = re.search(r'\s(IV|VV)\s', line)
            if rule_match:
                record['regra_voo'] = rule_match.group(1).replace('IV', 'IFR').replace('VV', 'VFR')
                before_rule = line[:rule_match.start()]
                after_rule = line[rule_match.end():]
            else:
                before_rule = line
                after_rule = ''

            if after_rule:
                record['responsavel'] = after_rule.strip().split()[-1]
                pista_match = re.search(r'(07|25)', after_rule)
                if pista_match:
                    record['pista'] = pista_match.group(1)

            main_data_block = re.sub(r'^SBIZAIZ\d+\s*', '', before_rule).strip()
            main_data_block = main_data_block.replace(record['matricula'], '', 1).strip()
            
            time_match = re.search(r'(\d{4})', main_data_block)
            horario_str = ''
            if time_match:
                horario_str = time_match.group(1)
                main_data_block = main_data_block.replace(horario_str, '', 1)

            icao_codes = re.findall(r'S[A-Z0-9]{3}', main_data_block)
            
            if len(icao_codes) >= 2:
                record['destino'] = icao_codes[0]
                record['origem'] = icao_codes[1]
            elif len(icao_codes) == 1:
                if record['pista']:
                    record['origem'] = icao_code
                    record['destino'] = icao_codes[0]
                else:
                    record['origem'] = icao_codes[0]
                    record['destino'] = icao_code
            else:
                record['origem'] = icao_code
                record['destino'] = icao_code
            
            remaining_block = main_data_block
            for code in icao_codes:
                remaining_block = remaining_block.replace(code, '')
            
            acft_match = re.search(r'^([A-Z0-9]+)([GSNM])', remaining_block.strip())
            if acft_match:
                record['tipo_aeronave'] = acft_match.group(1)
                record['flight_class'] = acft_match.group(2)
            
            if horario_str:
                try:
                    data_str_header = line[7:13]
                    dt_obj = datetime.strptime(f"{data_str_header}{horario_str}", '%d%m%y%H%M')
                    record['timestamp'] = dt_obj.isoformat() + 'Z'
                    if data_date is None: data_date = record['timestamp']
                except (ValueError, IndexError): pass

        except Exception as e:
            print(f"Linha não processada completamente: '{line.strip()}'. Erro: {e}")
        
        # Adiciona o registro à lista final, independentemente de quão completo ele esteja
        records.append(record)
    
    return {"records": records, "icao_code": "SBIZ", "data_date": data_date, "expected_total": expected_total}


# ... O restante do arquivo app.py permanece o mesmo, sem alterações ...
@app.route('/')
def index():
    return render_template('index.html')
@app.route('/api/upload', methods=['POST'])
def upload_file():
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
    except Exception as e:
        return jsonify({"error": "Token inválido ou expirado"}), 401
    if 'dataFile' not in request.files:
        return jsonify({"error": "Nenhum arquivo enviado"}), 400
    file = request.files['dataFile']
    if file.filename == '':
        return jsonify({"error": "Nome de arquivo inválido"}), 400
    try:
        content = io.StringIO(file.stream.read().decode("utf-8", errors='ignore')).getvalue()
        parsed_data = parse_data_file(content)
        return jsonify({
            "records": parsed_data["records"],
            "icao_code": parsed_data["icao_code"],
            "data_date": parsed_data["data_date"],
            "expected_total": parsed_data["expected_total"]
        })
    except Exception as e:
        return jsonify({"error": f"Erro ao processar o arquivo: {str(e)}"}), 500
@app.route('/api/save_records', methods=['POST'])
def save_records():
    user_id = None
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
    except Exception as e:
        return jsonify({"error": "Token inválido ou expirado"}), 401
    if not db:
        return jsonify({"error": "Conexão com o banco de dados não está disponível"}), 500
    try:
        data_to_save = request.get_json()
        records_to_save = data_to_save.get('records')
        icao_code = data_to_save.get('icao_code')
        data_date = data_to_save.get('data_date')
        if not records_to_save or not isinstance(records_to_save, list):
            return jsonify({"error": "Dados inválidos ou vazios"}), 400
        upload_ref = db.collection('flight_uploads').document()
        upload_ref.set({
            'userId': user_id, 'createdAt': firestore.SERVER_TIMESTAMP,
            'recordCount': len(records_to_save), 'icaoCode': icao_code, 'dataDate': data_date
        })
        batch = db.batch()
        for i, rec in enumerate(records_to_save):
            doc_ref = upload_ref.collection('records').document()
            batch.set(doc_ref, rec)
            if (i + 1) % 500 == 0:
                batch.commit()
                batch = db.batch()
        batch.commit()
        return jsonify({"success": True, "message": f"{len(records_to_save)} registros salvos com sucesso!", "documentId": upload_ref.id}), 201
    except Exception as e:
        print(f"ERRO ao salvar no Firestore: {e}")
        return jsonify({"error": f"Erro interno ao salvar os dados: {str(e)}"}), 500
@app.route('/api/get_uploads', methods=['GET'])
def get_uploads():
    user_id = None
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
    except Exception as e:
        return jsonify({"error": "Autenticação falhou"}), 401
    try:
        uploads_ref = db.collection('flight_uploads')
        query = uploads_ref.where('userId', '==', user_id).order_by('createdAt', direction=firestore.Query.DESCENDING)
        results = []
        for doc in query.stream():
            doc_data = doc.to_dict()
            results.append({
                'uploadId': doc.id, 'createdAt': doc_data['createdAt'].isoformat(),
                'recordCount': doc_data.get('recordCount'), 'icaoCode': doc_data.get('icaoCode', None),
                'dataDate': doc_data.get('dataDate', None)
            })
        return jsonify(results), 200
    except Exception as e:
        print(f"ERRO ao buscar uploads: {e}")
        return jsonify({"error": "Não foi possível buscar o histórico de uploads."}), 500
@app.route('/api/get_records/<upload_id>', methods=['GET'])
def get_records(upload_id):
    user_id = None
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
    except Exception as e:
        return jsonify({"error": "Autenticação falhou"}), 401
    try:
        upload_doc = db.collection('flight_uploads').document(upload_id).get()
        if not upload_doc.exists or upload_doc.to_dict()['userId'] != user_id:
            return jsonify({"error": "Acesso não autorizado ou upload não encontrado"}), 403
        records_ref = db.collection('flight_uploads').document(upload_id).collection('records')
        records = [doc.to_dict() for doc in records_ref.stream()]
        return jsonify(records), 200
    except Exception as e:
        print(f"ERRO ao buscar registros do upload {upload_id}: {e}")
        return jsonify({"error": "Não foi possível buscar os registros."}), 500
@app.route('/api/delete_upload/<upload_id>', methods=['DELETE'])
def delete_upload(upload_id):
    user_id = None
    try:
        auth_header = request.headers.get('Authorization')
        id_token = auth_header.split(' ').pop()
        decoded_token = auth.verify_id_token(id_token)
        user_id = decoded_token['uid']
    except Exception as e:
        return jsonify({"error": "Autenticação falhou"}), 401
    try:
        upload_ref = db.collection('flight_uploads').document(upload_id)
        upload_doc = upload_ref.get()
        if not upload_doc.exists:
            return jsonify({"error": "Upload não encontrado"}), 404
        if upload_doc.to_dict()['userId'] != user_id:
            return jsonify({"error": "Acesso não autorizado"}), 403
        records_ref = upload_ref.collection('records')
        docs = records_ref.limit(500).stream()
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        upload_ref.delete()
        print(f"Sucesso! Upload {upload_id} apagado pelo usuário {user_id}")
        return jsonify({"success": True, "message": "Registro apagado com sucesso!"}), 200
    except Exception as e:
        print(f"ERRO ao apagar o upload {upload_id}: {e}")
        return jsonify({"error": "Não foi possível apagar o registro."}), 500

if __name__ == '__main__':
    app.run(debug=True)