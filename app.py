import streamlit as st
import requests

# Set up Streamlit page
st.set_page_config(page_title="ACR Appropriateness Criteria RAG MVP", layout="wide")
st.title("ACR Appropriateness Criteria CDS Bot (Phase 1 Frontend)")

st.warning("FDA Non-Device CDS Guardrail: This software is intended to aid Health Care Professionals (HCPs) in making clinical decisions by retrieving relevant medical literature. It does not replace clinical judgment. Please independently review the source recommendations provided below.")

API_URL = "http://localhost:8000/v1/analyze"

query = st.text_input("Enter a clinical scenario (e.g., '70yo male with thunderclap headache'):")

if query:
    with st.spinner("Analyzing scenario and retrieving ACR guidelines via FHIR-compliant API..."):
        try:
            response = requests.post(API_URL, json={"text": query})
            
            if response.status_code == 200:
                data = response.json()
                
                # Show the intermediate mock FHIR mapping
                if data.get("mock_bundle_used"):
                    with st.expander("View Internal Mock FHIR Bundle"):
                        st.json(data["mock_bundle_used"])
                
                st.subheader("RAG Generated Response (PEA Format)")
                st.write(data["recommendation"])
                
                st.subheader("Independent Review of Recommendations (Source Provenance)")
                st.markdown("Below are the exact excerpts retrieved from the ACR database used to generate this response:")
                
                for i, doc in enumerate(data.get("sources", [])):
                    with st.expander(f"Source {i+1}: {doc['metadata'].get('source', 'Unknown Document')}"):
                        st.write(doc['content'])
            else:
                st.error(f"Error from API: {response.status_code} - {response.text}")
        except requests.exceptions.ConnectionError:
            st.error("Could not connect to the backend API. Is `uvicorn main:app --reload` running?")
        except Exception as e:
            st.error(f"Error communicating with backend: {e}")
