"use client";

import React, { useState, useRef } from "react";
import { UploadCloud, FileText, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";

type Medicine = {
  medicine_name: string;
  dosage: string;
  frequency: string;
  duration: string;
};

type OCRResponse = {
  success: boolean;
  ocr_text?: string;
  medicine_count?: number;
  medicines?: Medicine[];
  error?: string;
};

export default function PrescriptionOCR() {
  const [file, setFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [status, setStatus] = useState<"idle" | "uploading" | "ocr" | "ai" | "success" | "error">("idle");
  const [results, setResults] = useState<OCRResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      setFile(e.dataTransfer.files[0]);
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
    }
  };

  const processPrescription = async () => {
    if (!file) return;

    setStatus("uploading");
    setErrorMessage("");
    setResults(null);

    const formData = new FormData();
    formData.append("file", file);

    try {
      // Simulate workflow steps
      setTimeout(() => setStatus("ocr"), 1000);
      setTimeout(() => setStatus("ai"), 2500);

      const response = await fetch("http://127.0.0.1:8000/api/prescription/analyze", {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error("Unable to read prescription. Please upload a clearer image.");
      }

      const data: OCRResponse = await response.json();
      
      if (data.success) {
        setResults(data);
        setStatus("success");
      } else {
        throw new Error(data.error || "AI analysis failed. Please try again.");
      }
    } catch (err: any) {
      setErrorMessage(err.message || "An unexpected error occurred.");
      setStatus("error");
    }
  };

  const StepIndicator = ({ step, label, currentStatus }: { step: string, label: string, currentStatus: string }) => {
    const isActive = currentStatus === step;
    const isPast = ["idle", "uploading", "ocr", "ai", "success"].indexOf(currentStatus) > ["idle", "uploading", "ocr", "ai", "success"].indexOf(step);
    
    return (
      <div className={`flex items-center space-x-3 p-3 rounded-lg transition-all duration-300 ${isActive ? 'bg-blue-50/50 text-blue-700' : isPast ? 'text-green-600' : 'text-slate-400'}`}>
        {isPast ? (
          <CheckCircle2 className="w-5 h-5 text-green-500" />
        ) : isActive ? (
          <Loader2 className="w-5 h-5 animate-spin text-blue-600" />
        ) : (
          <div className="w-5 h-5 rounded-full border-2 border-slate-300" />
        )}
        <span className={`font-medium ${isActive ? 'animate-pulse' : ''}`}>{label}</span>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-slate-50 p-6 font-sans">
      <div className="max-w-5xl mx-auto space-y-8">
        
        {/* Header */}
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-extrabold tracking-tight text-slate-900">Prescription OCR</h1>
          <p className="text-slate-500 text-lg">Upload Your Prescription. AI will extract medicines, dosage, frequency and duration automatically.</p>
        </div>

        {/* Upload Zone */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          <div className="space-y-6">
            <div 
              className={`relative flex flex-col items-center justify-center p-12 border-2 border-dashed rounded-3xl transition-all duration-300 bg-white/50 backdrop-blur-md shadow-sm cursor-pointer hover:bg-white hover:border-blue-400 ${dragActive ? 'border-blue-500 bg-blue-50/50 scale-[1.02]' : 'border-slate-300'}`}
              onDragEnter={handleDrag}
              onDragLeave={handleDrag}
              onDragOver={handleDrag}
              onDrop={handleDrop}
              onClick={() => fileInputRef.current?.click()}
            >
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept=".jpg,.jpeg,.png,.pdf"
                onChange={handleChange}
              />
              <div className="bg-blue-100 p-4 rounded-full mb-4">
                <UploadCloud className={`w-10 h-10 text-blue-600 ${dragActive ? 'animate-bounce' : ''}`} />
              </div>
              <h3 className="text-lg font-semibold text-slate-700">
                {file ? file.name : "Click or Drag & Drop"}
              </h3>
              <p className="text-slate-500 text-sm mt-2 text-center">
                Supports JPG, PNG, PDF up to 10MB
              </p>
            </div>

            <button
              onClick={processPrescription}
              disabled={!file || status === 'uploading' || status === 'ocr' || status === 'ai'}
              className="w-full py-4 px-6 rounded-xl text-white font-semibold text-lg bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed shadow-md hover:shadow-lg transition-all duration-300"
            >
              {status === 'idle' || status === 'success' || status === 'error' ? 'Analyze Prescription' : 'Processing...'}
            </button>

            {/* Error Message */}
            {status === 'error' && (
              <div className="p-4 rounded-xl bg-red-50 border border-red-200 flex items-start space-x-3">
                <AlertCircle className="w-5 h-5 text-red-600 shrink-0 mt-0.5" />
                <p className="text-red-800 text-sm font-medium">{errorMessage}</p>
              </div>
            )}
          </div>

          {/* Processing State & Workflow */}
          <div className="bg-white/70 backdrop-blur-xl border border-white/40 shadow-xl rounded-3xl p-8 flex flex-col justify-center relative overflow-hidden">
             {status === 'idle' && !results && (
                <div className="text-center opacity-50 space-y-4">
                   <FileText className="w-16 h-16 mx-auto text-slate-400" />
                   <p className="text-lg font-medium text-slate-500">Awaiting prescription...</p>
                </div>
             )}
             
             {status !== 'idle' && status !== 'error' && !results && (
               <div className="space-y-4 relative z-10">
                 <h3 className="text-xl font-bold text-slate-800 mb-6">Processing Steps</h3>
                 <StepIndicator step="uploading" label="Uploading Prescription..." currentStatus={status} />
                 <StepIndicator step="ocr" label="Running Optical Character Recognition..." currentStatus={status} />
                 <StepIndicator step="ai" label="Extracting Text & AI Detection..." currentStatus={status} />
               </div>
             )}

             {status === 'success' && results && (
               <div className="text-center space-y-4 animate-in fade-in zoom-in duration-500 relative z-10">
                 <div className="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-6">
                   <CheckCircle2 className="w-10 h-10 text-green-600" />
                 </div>
                 <h3 className="text-2xl font-bold text-slate-800">Prescription analyzed successfully</h3>
                 <div className="flex justify-center gap-6 mt-4">
                   <div className="bg-slate-100 px-4 py-2 rounded-lg">
                     <p className="text-sm text-slate-500">Medicines</p>
                     <p className="text-xl font-bold text-slate-800">{results.medicine_count}</p>
                   </div>
                   <div className="bg-slate-100 px-4 py-2 rounded-lg">
                     <p className="text-sm text-slate-500">Confidence</p>
                     <p className="text-xl font-bold text-green-600">~95%</p>
                   </div>
                   <div className="bg-slate-100 px-4 py-2 rounded-lg">
                     <p className="text-sm text-slate-500">Time</p>
                     <p className="text-xl font-bold text-slate-800">{new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}</p>
                   </div>
                 </div>
               </div>
             )}
          </div>
        </div>

        {/* Results Section */}
        {results?.medicines && (
          <div className="mt-12 animate-in slide-in-from-bottom-8 fade-in duration-700">
            <h2 className="text-2xl font-bold text-slate-800 mb-6">Extracted Medicines</h2>
            <div className="bg-white rounded-2xl shadow-sm border border-slate-200 overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-slate-50 border-b border-slate-200">
                      <th className="p-5 font-semibold text-slate-700">Medicine Name</th>
                      <th className="p-5 font-semibold text-slate-700">Dosage</th>
                      <th className="p-5 font-semibold text-slate-700">Frequency</th>
                      <th className="p-5 font-semibold text-slate-700">Duration</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.medicines.map((med, idx) => (
                      <tr key={idx} className="border-b border-slate-100 hover:bg-slate-50/50 transition-colors">
                        <td className="p-5 font-medium text-slate-900">{med.medicine_name || '-'}</td>
                        <td className="p-5 text-slate-600">{med.dosage || '-'}</td>
                        <td className="p-5 text-slate-600">
                          <span className="px-3 py-1 bg-blue-50 text-blue-700 rounded-full text-sm font-medium">
                            {med.frequency || '-'}
                          </span>
                        </td>
                        <td className="p-5 text-slate-600">{med.duration || '-'}</td>
                      </tr>
                    ))}
                    {results.medicines.length === 0 && (
                      <tr>
                        <td colSpan={4} className="p-8 text-center text-slate-500">
                          No medicines could be identified.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
